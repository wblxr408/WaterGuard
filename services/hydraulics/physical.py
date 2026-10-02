"""Physics-only checks; business objectives and recommendation remain outside this module."""
import math


def assess(wn, raw, mapping, times):
    maximum_residual = 0.0
    maximum_leak_relative_error = 0.0
    maximum_inactive_leak = 0.0
    for t in times:
        for node in wn.junction_name_list:
            outflow = float(raw.node['demand'].at[t,node])
            leaks = float(raw.node['leak_demand'].at[t,node]) if 'leak_demand' in raw.node else 0.0
            residual = -outflow - leaks
            for name in wn.get_links_for_node(node):
                p = wn.get_link(name)
                flow = float(raw.link['flowrate'].at[t,name])
                residual += flow if p.end_node_name==node else -flow
            maximum_residual = max(maximum_residual,abs(residual))
        for spec in mapping['leaks']:
            node = spec['node_id']
            pressure = float(raw.node['pressure'].at[t,node])
            actual = float(raw.node['leak_demand'].at[t,node])
            active = mapping['fault_window'][0] <= t < mapping['fault_window'][1]
            if not active:
                maximum_inactive_leak=max(maximum_inactive_leak,abs(actual))
            # Check expected emission even when an erroneous solver/control returned zero.
            if active and pressure > 1e-3:
                expected = spec['discharge_coefficient']*spec['area_m2']*math.sqrt(2*9.81*pressure)
                maximum_leak_relative_error = max(maximum_leak_relative_error,abs(actual-expected)/expected)
    pump_error=0.0
    for _,pump in wn.pumps():
        a,b,c=pump.get_head_curve_coefficients()
        for q,h in wn.get_curve(pump.pump_curve_name).points:
            pump_error=max(pump_error,abs(a-b*q**c-h))
    return dict(kind='physical_checks',mass_balance_max_abs_m3s=maximum_residual,
                mass_balance_tolerance_m3s=2e-6,leak_law_max_relative_error=maximum_leak_relative_error,
                leak_law_tolerance=1e-4,pump_curve_max_fit_error_m=pump_error,pump_curve_fit_tolerance_m=1e-4,
                inactive_leak_max_m3s=maximum_inactive_leak,
                passed=maximum_residual<=2e-6 and maximum_leak_relative_error<=1e-4 and pump_error<=1e-4 and maximum_inactive_leak<=1e-10,
                interpretation='Junction continuity and active positive-pressure orifice law; not independent field validation.')
