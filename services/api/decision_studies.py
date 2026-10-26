"""Small, evidence-backed scenario studies composed from existing hydraulic tools."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from typing import Literal
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, Field

from waterguard_contracts.core import check_plan, check_task, plan_hash, require, task_hash, task_ref
from services.hydraulics.configuration import demand_domain, ranking_key
from services.hydraulics.runtime import PhysicalAdapter
from services.verifier.service import dense_frames, verify
from .runtime import NotFoundError, RunStore, now
from .workbench import benchmark_plans, network_details


class ExtraAction(BaseModel):
    kind: Literal['link_open', 'link_close', 'pump_on', 'pump_off']
    link_id: str
    simulation_time_s: int = Field(ge=0)


class RescueOption(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    cost: float = Field(ge=0, allow_inf_nan=False)
    repair_end_s: int | None = None
    extra_action: ExtraAction | None = None


class StudyRequest(BaseModel):
    kind: Literal['boundary', 'rescue', 'event'] = 'boundary'
    task: dict
    plan: dict
    source_run_id: str | None = None
    demand_multipliers: list[float] = Field(default_factory=lambda: [.9, 1, 1.1, 1.2, 1.3], min_length=1, max_length=15)
    repair_end_times_s: list[int] = Field(default_factory=list, max_length=15)
    rescue_options: list[RescueOption] = Field(default_factory=list, max_length=30)
    rescue_target: Literal['normal', 'basic'] = 'normal'
    event_time_s: int | None = None
    response_time_s: int = Field(default=0, ge=0, le=7200)
    new_repair_end_s: int | None = None
    max_simulations: int = Field(default=9, ge=1, le=64)
    max_wall_time_s: int = Field(default=300, ge=1, le=3600)
    use_surrogate: bool = False
    high_fidelity_budget: int = Field(default=3, ge=1, le=32)


def classify(report):
    if report['validity'] != 'valid' or report['admission']['simulation_valid'] != 'pass':
        return 'invalid'
    if not report['violations']:
        return 'normal'
    m = report['metrics']
    if m.get('assessed_node_count', 0) > 0 and m.get('nodes_reaching_emergency_service') == m['assessed_node_count']:
        return 'basic'
    return 'gap'


def remaining_outcome(task, frames, start_s):
    """Integrate the verified raw intervals over the remaining event horizon."""
    nodes = {node: dict(normal_s=0, basic_s=0) for node in task['hard_constraints']['node_ids']}
    normal, basic = task['hard_constraints'], task.get('emergency_floor')
    shortage = duration = 0.
    for frame in frames:
        dt = max(0, frame['time_s']+frame['duration_s']-max(start_s, frame['time_s']))
        duration += dt
        for row in frame['nodes']:
            d, q, p = row['expected_demand_m3s'], row['delivered_demand_m3s'], row['pressure_m']
            ratio = min(1., q/d) if d > 0 else 1.
            shortage += max(0, d-q)*dt
            passed = lambda gate: p+normal['pressure_tolerance_m'] >= gate['min_pressure_m'] and ratio+normal['ratio_tolerance'] >= gate['min_supply_ratio']
            nodes[row['node_id']]['normal_s'] += dt*passed(normal)
            nodes[row['node_id']]['basic_s'] += dt*bool(basic and passed(basic))
    normal_ratios=[v['normal_s']/duration for v in nodes.values()]
    basic_ratios=[v['basic_s']/duration for v in nodes.values()]
    priorities=task.get('service_priorities',{}).get('node_ids',[])
    return dict(start_s=start_s, end_s=task['time_window']['end_s'], shortage_m3=shortage,
        assessed_node_count=len(nodes),fully_compliant_node_count=sum(r>=1-1e-9 for r in normal_ratios),
        min_node_time_pass_ratio=min(normal_ratios),
        nodes_reaching_emergency_service=sum(r>=1-1e-9 for r in basic_ratios),
        priority_nodes_reaching_emergency_service=sum(nodes[n]['basic_s']/duration>=1-1e-9 for n in priorities),
        mean_node_time_pass_ratio=sum(v['normal_s'] for v in nodes.values())/(duration*len(nodes)),
        emergency_mean_node_time_pass_ratio=None if basic is None else sum(v['basic_s'] for v in nodes.values())/(duration*len(nodes)),
        per_node=[dict(node_id=node, **v) for node, v in nodes.items()])


def next_boundary(points):
    pending = [p for p in points if p['state'] == 'unknown']
    measured = [p for p in points if p['state'] not in ('unknown', 'invalid')]
    coords = np.array([[p['demand_multiplier'], p['repair_end_s']] for p in points], dtype=float)
    span = np.ptp(coords, axis=0); span[span == 0] = 1
    normalized = {p['point_id']: (c-coords.min(axis=0))/span for p, c in zip(points, coords)}
    if not measured:
        return min(pending, key=lambda p: float(np.linalg.norm(normalized[p['point_id']]-.5))), '覆盖网格中心'
    def score(p):
        distances = sorted((float(np.linalg.norm(normalized[p['point_id']]-normalized[q['point_id']])), q['point_id'], q) for q in measured)
        local = [q for _, _, q in distances[:4]]
        disagreement = len({q['state'] for q in local}) > 1
        return 2*disagreement+distances[0][0]
    chosen = max(pending, key=score)
    return chosen, '优先状态变化邻域' if score(chosen) >= 2 else '扩展未测区域'


class DecisionStudies:
    def __init__(self, store: RunStore):
        self.store = store
        self.root = store.artifacts/'decision-studies'
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='decision-study')
        self.lock = threading.RLock()
        self.active = {}

    def _folder(self, study_id):
        UUID(study_id.removeprefix('study-'))
        return self.root/study_id

    def _save(self, study):
        self.store._write_json(f"decision-studies/{study['study_id']}/study.json", study)

    def get(self, study_id):
        path = self._folder(study_id)/'study.json'
        if not path.is_file():
            raise NotFoundError('Unknown decision study')
        with self.lock:
            return self.store._read_json(path)

    def recent(self):
        return sorted((self.store._read_json(p) for p in self.root.glob('*/study.json')), key=lambda s: s['created_at'], reverse=True)[:20]

    def recover(self):
        for study in self.recent():
            if study['status'] in ('QUEUED', 'RUNNING'):
                study['status'] = 'INTERRUPTED'
                self._save(study)

    def preview(self, request: StudyRequest):
        source, source_plan = copy.deepcopy(request.task), copy.deepcopy(request.plan)
        details = network_details(source['network_ref']['network_id'])
        check_task(source, details['catalog'], details['scene'])
        check_plan(source, source_plan, details['catalog'])
        require(source['fault'] is not None, 'Decision studies require a fault scenario')
        require(source_plan['task_ref'] == task_ref(source), 'Plan belongs to another task')
        if request.use_surrogate:
            require(request.kind == 'boundary', 'Multi-fidelity screening is available for boundary studies')
            require(2 <= request.high_fidelity_budget <= request.max_simulations-2, 'Reserve at least two high-fidelity and two coarse runs')
            require(source['hydraulic_options']['hydraulic_timestep_s'] < source['time_window']['step_s'], 'No coarser hydraulic step is available in this task')
        study_id = 'study-'+uuid4().hex
        window, fault = source['time_window'], source['fault']
        step, points = window['step_s'], []
        def add(multiplier, repair, *, label='', cost=0., extra=None, actions=None):
            require(np.isfinite(multiplier) and 0 < multiplier <= 5, 'Demand multiplier must be within (0, 5]')
            require(fault['start_s'] < repair <= window['end_s'] and repair % step == 0,
                    'Repair time must be a reporting boundary after the fault and within the horizon')
            task, plan = copy.deepcopy(source), copy.deepcopy(source_plan)
            task['task_id'] = study_id+f'-{len(points)}'
            task['fault']['end_s'] = repair
            nominal = source['hydraulic_options']['demand_multiplier']
            increase = (max(source['perturbation_domain']['demand_multipliers'])/nominal-1)*100
            task['hydraulic_options']['demand_multiplier'] = multiplier
            task['perturbation_domain'] = demand_domain(increase, multiplier)
            if actions is not None:
                plan['actions'] = copy.deepcopy(actions)
            if extra is not None:
                facility = next(f for f in details['catalog']['facilities'] if f['link_id'] == extra['link_id'])
                require(extra['kind'] in facility['allowed_actions'], 'Unsupported facility operation')
                if extra['link_id'] not in task['action_limits']['allowed_link_ids']:
                    task['action_limits']['allowed_link_ids'].append(extra['link_id'])
                if extra['kind'] not in task['action_limits']['allowed_kinds']:
                    task['action_limits']['allowed_kinds'].append(extra['kind'])
                if facility['requires_explicit_override'] and not any(o['link_id'] == extra['link_id'] for o in task['control_policy']['overrides']):
                    task['control_policy']['overrides'].append(dict(link_id=extra['link_id'],control_names=facility['native_control_names'],reason='User-declared hypothetical rescue permission.'))
                plan['actions'].append(extra)
                task['action_limits']['max_actions'] = max(task['action_limits']['max_actions'],len(plan['actions']))
            task['constraint_hash'] = task_hash(task)
            plan.update(task_ref=task_ref(task), candidate_id=f'study-candidate-{len(points)}', parent_candidate_id=None)
            plan['plan_hash'] = plan_hash(plan)
            check_task(task, details['catalog'], details['scene'])
            check_plan(task, plan, details['catalog'])
            points.append(dict(point_id=f'point-{len(points)}', demand_multiplier=multiplier, repair_end_s=repair,
                label=label, cost=cost, state='unknown', task=task, plan=plan))
        nominal = source['hydraulic_options']['demand_multiplier']
        if request.kind == 'boundary':
            repairs = request.repair_end_times_s or sorted({max(fault['start_s']+step,fault['end_s']-step),fault['end_s'],min(window['end_s'],fault['end_s']+step)})
            for repair in sorted(set(repairs)):
                for multiplier in sorted(set(request.demand_multipliers)):
                    add(multiplier, repair)
        elif request.kind == 'rescue':
            add(nominal, fault['end_s'], label='原方案', cost=0)
            require(bool(request.rescue_options), 'Declare at least one rescue option')
            for option in sorted(request.rescue_options, key=lambda o: o.cost):
                require(option.repair_end_s is not None or option.extra_action is not None, 'Rescue option needs an actual change')
                if option.repair_end_s is not None:
                    require(option.repair_end_s < fault['end_s'], 'Rescue repair must be earlier than the current repair')
                add(nominal, option.repair_end_s or fault['end_s'], label=option.label, cost=option.cost,
                    extra=None if option.extra_action is None else option.extra_action.model_dump())
        else:
            event = request.event_time_s
            require(event is not None and window['evaluation_start_s'] <= event < window['end_s'], 'Event must be in the evaluation horizon')
            require(event < fault['end_s'], 'Repair event must occur before the original fault has already ended')
            require(request.new_repair_end_s is not None and request.new_repair_end_s > max(event,fault['start_s']), 'New repair must follow the event and fault')
            floor = max(source['action_limits']['earliest_s'], int(np.ceil((event+request.response_time_s)/step))*step)
            require(floor <= source['action_limits']['latest_s'], 'No action slot remains after response time')
            # Keep already executed controls; unexecuted original controls cannot be backdated on reassessment.
            prefix = [a for a in source_plan['actions'] if a['simulation_time_s'] < event]
            retained = prefix+[dict(a,simulation_time_s=max(a['simulation_time_s'],floor)) for a in source_plan['actions'] if a['simulation_time_s'] >= event]
            add(nominal, request.new_repair_end_s, label='原计划延续', actions=retained)
            future_task = copy.deepcopy(points[0]['task'])
            future_task['action_limits']['earliest_s'] = floor
            future_task['constraint_hash'] = task_hash(future_task)
            for candidate in benchmark_plans(future_task, details['catalog']):
                actions = prefix+candidate['actions']
                if actions == retained or len(actions) > source['action_limits']['max_actions']:
                    continue
                try:
                    add(nominal, request.new_repair_end_s, label='更新后续动作', actions=actions)
                except ValueError:
                    # Candidate composition may violate the existing switch interval; omit that combination.
                    continue
        return dict(study_id=study_id, kind=request.kind, status='QUEUED', created_at=now(),
            source_task_ref=task_ref(source), source_candidate_id=source_plan['candidate_id'], source_run_id=request.source_run_id,
            request=request.model_dump(), points=points, simulations=0, selected_point_id=None,
            max_simulations=request.max_simulations, max_wall_time_s=request.max_wall_time_s, elapsed_s=0.,
            fidelity_counts=dict(low=0, high=0),
            scope='Tested scenario results; not a continuous feasibility guarantee or a live dispatch recommendation.')

    def create(self, request):
        study = self.preview(request)
        cancel = threading.Event()
        with self.lock:
            self._save(study)
            self.active[study['study_id']] = cancel
            self.pool.submit(self._run, study, cancel)
        return study

    def cancel(self, study_id):
        study = self.get(study_id)
        if study_id in self.active:
            self.active[study_id].set()
        return study

    def evaluate(self, task, plan, directory, *, cancel_event, timeout_s, event_time_s=None, fidelity='high'):
        started=time.monotonic()
        if fidelity == 'low':
            task, plan = copy.deepcopy(task), copy.deepcopy(plan)
            task['task_id'] += '-coarse'
            task['hydraulic_options']['hydraulic_timestep_s'] = task['time_window']['step_s']
            task['constraint_hash'] = task_hash(task)
            plan['task_ref'] = task_ref(task)
            plan['plan_hash'] = plan_hash(plan)
        adapter = PhysicalAdapter(directory/'simulations')
        result = adapter.simulate(task, plan, timeout_s=timeout_s, cancel_event=cancel_event)
        sid = result['artifact']['simulation_id']
        folder = adapter.output_root/sid
        details = network_details(task['network_ref']['network_id'])
        report = verify(task, plan, result, details['catalog'], details['scene'], artifact_dir=folder)
        from services.hydraulics.storage import write
        write(directory/'verification.json', report)
        outcome = dict(state=classify(report), metrics=report['metrics'], report=report, simulation_id=sid,
            evidence_ref=str(folder.relative_to(self.store.artifacts)), actions=plan['actions'], fidelity=fidelity,
            elapsed_s=time.monotonic()-started)
        if outcome['state'] != 'invalid':
            import pandas as pd
            pressures = pd.read_csv(folder/'raw-node-pressure.csv',index_col='time_s')
            pressures = pressures.loc[(pressures.index >= task['time_window']['evaluation_start_s']) & (pressures.index < task['time_window']['end_s']),task['hard_constraints']['node_ids']]
            outcome['node_pressure_m'] = {n:float(v) for n,v in pressures.min().items()}
        if event_time_s is not None and outcome['state'] != 'invalid':
            outcome['remaining'] = remaining_outcome(task, dense_frames(folder, task), event_time_s)
        return outcome

    def _run(self, study, cancel):
        started = time.monotonic()
        try:
            study['status'] = 'RUNNING'
            with self.lock: self._save(study)
            if study['request']['use_surrogate']:
                self._multifidelity(study, cancel, started)
                return
            while any(p['state'] == 'unknown' for p in study['points']):
                remaining = study['max_wall_time_s']-(time.monotonic()-started)
                if cancel.is_set() or remaining <= 0 or study['simulations'] >= study['max_simulations']:
                    break
                if study['kind'] == 'boundary':
                    point, reason = next_boundary(study['points'])
                else:
                    point = next(p for p in study['points'] if p['state'] == 'unknown')
                    reason = '同条件对照' if study['simulations'] == 0 else '按声明成本测试' if study['kind'] == 'rescue' else '保留历史、更新后续动作'
                point['sampling_reason'] = reason
                outcome = self.evaluate(point['task'], point['plan'], self._folder(study['study_id'])/point['point_id'],
                    cancel_event=cancel, timeout_s=remaining, event_time_s=study['request']['event_time_s'] if study['kind']=='event' else None)
                point.update(outcome)
                study['simulations'] += 1
                study['fidelity_counts']['high'] += 1
                study['elapsed_s'] = time.monotonic()-started
                self._select(study)
                with self.lock: self._save(study)
            study['status'] = 'CANCELLED' if cancel.is_set() else 'BUDGET_EXHAUSTED' if time.monotonic()-started >= study['max_wall_time_s'] else 'COMPLETED'
            study['budget_exhausted'] = any(p['state']=='unknown' for p in study['points']) and not cancel.is_set()
        except Exception as error:
            study.update(status='CANCELLED' if cancel.is_set() else 'ERROR', error=str(error))
        finally:
            study['elapsed_s'] = time.monotonic()-started
            with self.lock:
                self._save(study)
                self.active.pop(study['study_id'], None)

    def _multifidelity(self, study, cancel, started):
        from services.hydraulics.surrogate import PhysicsResidualSurrogate
        points = study['points']
        model = PhysicsResidualSurrogate(study['request']['task'])
        high_budget = min(study['request']['high_fidelity_budget'],len(points))
        low_budget = min(study['max_simulations']-high_budget,len(points))
        high_budget = min(high_budget,low_budget)
        def available():
            return not cancel.is_set() and time.monotonic()-started < study['max_wall_time_s']
        def evaluate(point, fidelity):
            result = self.evaluate(point['task'],point['plan'],self._folder(study['study_id'])/point['point_id']/fidelity,
                cancel_event=cancel,timeout_s=study['max_wall_time_s']-(time.monotonic()-started),fidelity=fidelity)
            study['simulations'] += 1
            study['fidelity_counts'][fidelity] += 1
            return result
        for _ in range(low_budget):
            if not available(): break
            # Reuse spatial exploration without allowing coarse labels into final point states.
            sampling = [dict(p,state=p.get('low_fidelity',{}).get('state','unknown')) for p in points]
            chosen, _ = next_boundary(sampling)
            point = next(p for p in points if p['point_id']==chosen['point_id'])
            point['low_fidelity'] = evaluate(point,'low')
            point['sampling_reason'] = '粗仿真覆盖情景空间'
            self._save(study)
        errors=[]
        for _ in range(high_budget):
            eligible = [p for p in points if p['state']=='unknown' and p.get('low_fidelity',{}).get('node_pressure_m')]
            if not available() or not eligible: break
            model.fit(points)
            for p in eligible: p['prediction'] = model.predict(p)
            point = max(eligible,key=lambda p:p['prediction']['acquisition_score'])
            previous = point['prediction'].copy()
            point.update(evaluate(point,'high'))
            point['sampling_reason'] = '代理临界性与经验不确定度优先复验'
            if point['state'] != 'invalid':
                error=abs(previous['min_pressure_m']-min(point['node_pressure_m'].values()))
                point['prequential_error_m']=error
                errors.append(error)
            model.fit(points)
            study['surrogate']=dict(**model.describe(),prequential_count=len(errors),
                prequential_mae_m=float(np.mean(errors)) if errors else None)
            study['elapsed_s']=time.monotonic()-started
            self._save(study)
        model.fit(points)
        for p in points:
            if p['state']=='unknown' and p.get('low_fidelity',{}).get('node_pressure_m'):
                p['prediction']=model.predict(p)
        study['surrogate']=dict(**model.describe(),prequential_count=len(errors),prequential_mae_m=float(np.mean(errors)) if errors else None)
        checkpoint=self._folder(study['study_id'])/'surrogate.pt'
        model.save(checkpoint)
        study['surrogate']['checkpoint_ref']=str(checkpoint.relative_to(self.store.artifacts))
        study['status']='CANCELLED' if cancel.is_set() else 'BUDGET_EXHAUSTED' if not available() else 'COMPLETED'
        study['budget_exhausted']=any(p['state']=='unknown' for p in points) and not cancel.is_set()

    @staticmethod
    def _select(study):
        valid = [p for p in study['points'] if p['state'] not in ('unknown','invalid')]
        if study['kind'] == 'rescue':
            target = study['request']['rescue_target']
            valid = [p for p in valid if p['state'] == 'normal' or target == 'basic' and p['state'] == 'basic']
            best = min(valid,key=lambda p:p['cost'],default=None)
        elif study['kind'] == 'event':
            def key(p):
                r = p.get('remaining')
                if r is not None:
                    report=dict(p['report'],metrics=dict(p['report']['metrics'],**r))
                    return ranking_key(p['task'],report)
                return ranking_key(p['task'],p['report'])
            best = min(valid,key=key,default=None)
        else:
            best = None
        study['selected_point_id'] = None if best is None else best['point_id']

    def close(self):
        with self.lock:
            for cancel in self.active.values(): cancel.set()
        self.pool.shutdown(wait=True)
