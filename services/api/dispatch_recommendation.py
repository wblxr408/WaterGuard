"""Separate a preferred emergency dispatch from full hydraulic admission."""


def dispatch_recommendation(summary, candidates):
    """Use the host's frozen ranking; recommending dispatch never rewrites full admission."""
    if summary.get('native_provenance_valid') is not True:
        return None
    indexed = {c['id']: c for c in candidates}
    for cid in [summary.get('selected_candidate_id'), summary.get('mitigation_candidate_id'),
                summary.get('best_effort_candidate_id')]:
        c = indexed.get(cid)
        if not c or c['verification']['validity'] != 'valid':
            continue
        complete = c['verification']['admission']['recommendation_eligible'] == 'pass'
        return dict(candidate_id=cid, tier='fully_compliant' if complete else 'best_effort', fully_compliant=complete)
    return None
