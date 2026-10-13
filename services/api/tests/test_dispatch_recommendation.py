"""An emergency dispatch recommendation preserves full admission and requires actual verified evidence."""
import unittest

from services.api.dispatch_recommendation import dispatch_recommendation


def candidate(cid, *, valid=True, admitted=False):
    return dict(id=cid, verification=dict(validity='valid' if valid else 'invalid',
        admission=dict(recommendation_eligible='pass' if admitted else 'fail'),
        violations=[] if admitted else [dict(node_id='n', code='MIN_PRESSURE')]))


class DispatchRecommendationTests(unittest.TestCase):
    def test_complete_admission_takes_priority_over_partial_supply(self):
        result = dispatch_recommendation(dict(native_provenance_valid=True, selected_candidate_id='complete',
            mitigation_candidate_id='rescue', best_effort_candidate_id='rescue'),
            [candidate('complete', admitted=True), candidate('rescue')])
        self.assertEqual(result, dict(candidate_id='complete', tier='fully_compliant', fully_compliant=True))

    def test_best_effort_is_recommended_for_dispatch_but_keeps_failed_full_admission(self):
        rescue = candidate('rescue')
        result = dispatch_recommendation(dict(native_provenance_valid=True, mitigation_candidate_id='rescue',
            best_effort_candidate_id='rescue'), [rescue])
        self.assertEqual(result, dict(candidate_id='rescue', tier='best_effort', fully_compliant=False))
        self.assertEqual(rescue['verification']['admission']['recommendation_eligible'], 'fail')
        self.assertEqual(len(rescue['verification']['violations']), 1)

    def test_no_improving_control_can_still_recommend_the_best_verified_baseline(self):
        result = dispatch_recommendation(dict(native_provenance_valid=True, mitigation_candidate_id=None,
            selected_candidate_id=None, best_effort_candidate_id='baseline'), [candidate('baseline')])
        self.assertEqual(result, dict(candidate_id='baseline', tier='best_effort', fully_compliant=False))

    def test_incomplete_native_or_invalid_physical_evidence_is_not_recommended(self):
        self.assertIsNone(dispatch_recommendation(dict(native_provenance_valid=False, best_effort_candidate_id='a'), [candidate('a')]))
        self.assertIsNone(dispatch_recommendation(dict(native_provenance_valid=True, best_effort_candidate_id='a'), [candidate('a', valid=False)]))
        self.assertIsNone(dispatch_recommendation(dict(native_provenance_valid=True, best_effort_candidate_id='missing'), []))


if __name__ == '__main__':
    unittest.main()
