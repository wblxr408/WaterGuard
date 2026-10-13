"""Server-owned next-step constraints derived only from persisted reports."""


def reviews(store, task, children):
    required = set(task['perturbation_domain']['required'])
    nominal, stress = [], {}
    for child in children:
        root = store._run_directory(child['run_id'])
        perturbation = store._read_json(root / 'perturbation.json') if (root / 'perturbation.json').is_file() else None
        for path in (root / 'candidates').glob('*/verification.json'):
            report = store._read_json(path)
            plan = store._read_json(path.with_name('plan.json'))
            if report['task_ref'] != child['task_ref']:
                raise ValueError('Workflow report task binding mismatch')
            passed = report['validity'] == 'valid' and not report['violations']
            if perturbation is None:
                nominal.append(dict(run_id=child['run_id'], candidate_id=report['candidate_id'],
                    plan_hash=plan['plan_hash'], plan_actions=plan['actions'], plan_source=plan['source'], passed=passed, report=report))
            elif report['validity'] == 'valid':
                key = (report['candidate_id'], plan['plan_hash'])
                record = stress.setdefault(key, dict(tested=set(), passed=set()))
                record['tested'].update(perturbation['required_labels'])
                if passed:
                    record['passed'].update(perturbation['required_labels'])
    pending = [row for row in nominal if row['passed'] and not required <=
               stress.get((row['candidate_id'], row['plan_hash']), {}).get('tested', set())]
    complete = [row for row in nominal if row['passed'] and required <=
                stress.get((row['candidate_id'], row['plan_hash']), {}).get('passed', set())]
    return nominal, pending, complete


def livelihood_gap(task, report):
    """Users still without basic service in the best tested plan; None when the report cannot say."""
    m = report.get('metrics') or {}
    served_key = 'nodes_reaching_emergency_service' if 'emergency_floor' in task else 'fully_compliant_node_count'
    served, total = m.get(served_key), m.get('assessed_node_count')
    if served is None or total is None:
        return None
    priority_unserved = 0
    if 'service_priorities' in task:
        declared, reached = m.get('priority_node_count'), m.get('priority_nodes_reaching_emergency_service')
        if declared is None or reached is None:
            return None
        priority_unserved = declared - reached
    return dict(unserved_nodes=total - served, priority_unserved_nodes=priority_unserved, assessed_node_count=total)


def first_aid_allotment(task, remaining, tested):
    """How many enumerated first-aid plans may be simulated, keeping room to refine and to stress-review."""
    from services.hydraulics.configuration import defaults
    config = defaults().get('first_aid', {})
    return max(0, min(config.get('limit', 4), remaining + tested - config.get('reserve_simulations', 2)))


def workflow_hint(store, parent):
    from .workbench import benchmark_plans, network_details
    task = store.get_task(parent['task_ref']['task_id'], parent['task_ref']['revision'])
    children = store.live_children(parent['run_id'])
    nominal, pending, complete = reviews(store, task, children)
    remaining = max(0, task['budget']['max_simulations'] - parent['budget_usage']['simulations'])
    result = dict(version='waterguard-workflow-guard/1.0.0', owner='server-derived governance; not a Jev model decision',
        parent_run_id=parent['run_id'], remaining_simulations=remaining,
        compare_run_ids=[row['run_id'] for row in nominal],
        finalization_tools=['wg_compare_candidates', 'wg_build_evidence'])
    if complete or remaining == 0:
        return dict(result, required_next='compare_and_archive',
                    reason='Hydraulic review is complete or no simulation budget remains. Do not create another simulation.')
    if pending:
        from services.hydraulics.compiler import validate_perturbation
        from services.hydraulics.configuration import stress_multiplier
        multiplier = stress_multiplier(task)
        # The same label/physics check used by the actual simulator must also
        # govern the next-step hint; it cannot silently fall back to a fixed x.
        validate_perturbation(task, {'candidate_id': pending[0]['candidate_id']},
            dict(perturbation_id='workflow-preflight', candidate_id=pending[0]['candidate_id'],
                 parameters={'demand_multiplier': multiplier}, required_labels=task['perturbation_domain']['required']))
        return dict(result, required_next='create_perturbation', source_run_id=pending[0]['run_id'],
            candidate_id=pending[0]['candidate_id'], demand_multiplier=multiplier,
            reason='A nominally compliant candidate must use the same plan for its required stress review before more candidates.')
    tested = {row['candidate_id'] for row in nominal}
    plans = benchmark_plans(task, network_details(task['network_ref']['network_id'])['catalog'])
    from .planning import revision_records
    revisions = revision_records(store, parent)
    plans.extend(row['plan'] for row in revisions)
    untested_revisions = [r['plan']['candidate_id'] for r in revisions if r['plan']['candidate_id'] not in tested]
    action_failures = [row for row in nominal if not row['passed'] and row['report']['validity'] == 'valid' and row['plan_actions']]
    revision_tested = any(row['plan_source'] == 'agh' for row in nominal)
    baseline_failed = any(not row['plan_actions'] and not row['passed'] and row['report']['validity'] == 'valid' for row in nominal)
    first_aid_done = sum(1 for row in nominal if row['plan_actions'] and row['plan_source'] != 'agh')
    allotment = first_aid_allotment(task, remaining, first_aid_done)
    if baseline_failed and not revision_tested and task['action_limits']['max_actions'] > 0 and first_aid_done < allotment:
        untested = [p['candidate_id'] for p in plans if p['actions'] and p['source'] != 'agh' and p['candidate_id'] not in tested]
        return dict(result, required_next='first_aid', first_aid_candidate_ids=untested[:allotment - first_aid_done],
            first_aid_allotment=allotment, first_aid_tested=first_aid_done,
            reason='The no-action baseline fails. Simulate the listed first-aid candidates first: they are the fixed, fast, '
                   'topology-ranked controls. Only a remaining service gap in the best of them calls for model refinement.')
    from services.hydraulics.configuration import ranking_key
    best = min(action_failures, key=lambda row: ranking_key(task, row['report'])) if action_failures else None
    gap = livelihood_gap(task, best['report']) if best else None
    if best and not revision_tested and gap is not None and gap['unserved_nodes'] == 0 and gap['priority_unserved_nodes'] == 0:
        return dict(result, required_next='compare_and_archive', refinement_required=False, livelihood_gap=gap,
            best_first_aid=dict(run_id=best['run_id'], candidate_id=best['candidate_id']),
            reason='Every assessed user keeps basic service in the best first-aid plan, so no model refinement is requested. '
                   'Remaining normal-tier violations stay on the record; compare and archive.')
    if (task['schema_version'] == '0.4.0' and action_failures and not revision_tested and remaining >= 2 and
            task['action_limits']['max_actions'] > 0 and (untested_revisions or len(revisions) < task['budget']['max_revisions'])):
        return dict(result, required_next='simulate_revision' if untested_revisions else 'diagnose_and_revise',
            candidate_ids=untested_revisions, source_run_id=best['run_id'], source_candidate_id=best['candidate_id'],
            refinement_required=True, livelihood_gap=gap, first_aid_candidate_ids=[],
            revision_budget_remaining=max(0, task['budget']['max_revisions'] - len(revisions)),
            revision_tools=['wg_diagnose_failure', 'wg_revise_plan'],
            reason='The best first-aid plan still leaves users without basic service or priority points uncovered. Diagnose that real failure '
                   '(facility_effects shows where the water actually goes), register revised controls and simulate them. The revision is never a predicted pass.')
    return dict(result, required_next='choose_candidate', candidate_ids=[p['candidate_id'] for p in plans if p['candidate_id'] not in tested],
                revision_budget_remaining=max(0, task['budget']['max_revisions'] - len(revisions)),
                failed_source_runs=[dict(run_id=row['run_id'], candidate_id=row['candidate_id']) for row in nominal
                                    if not row['passed'] and row['report']['validity'] == 'valid'],
                revision_tools=['wg_diagnose_failure', 'wg_revise_plan'],
                reason='Use the no-action baseline first. After a valid failure, select an untested permitted action or revise controls from its independently checked witness. A revision is not a predicted pass.')
