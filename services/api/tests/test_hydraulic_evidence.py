"""Facility effect evidence is observed from original flows, not predicted."""
import unittest

import services.api.runtime  # noqa: F401  (puts the contracts package on sys.path)
from services.api.hydraulic_evidence import facility_effects

#   R --1--> A --2--> B --3--> C        link 4 carries water A --> D (fault pipe 5 joins D and E)
#                     \--6--> F         link 7 is idle, link 8 flows backwards (G --> A)
LINKS = {'1': ('R', 'A'), '2': ('A', 'B'), '3': ('B', 'C'), '4': ('A', 'D'), '5': ('D', 'E'),
         '6': ('B', 'F'), '7': ('C', 'F'), '8': ('A', 'G')}
FLOWS = {'1': 5.0, '2': 3.0, '3': 1.0, '4': 1.5, '5': 0.5, '6': 1.0, '7': 0.0, '8': -0.4}
FAILED = {'C': 2.0, 'F': 1.0, 'E': 1.0}
FAULT = ('D', 'E')


def effects(ids=('1', '2', '3', '4', '6', '7', '8'), flows=FLOWS):
    rows = facility_effects(LINKS, flows, FAILED, FAULT, ids)
    return {row['link_id']: row for row in rows}


class FacilityEffectTests(unittest.TestCase):
    def test_downstream_failed_nodes_and_demand_share_follow_the_flow_direction(self):
        two = effects()['2']
        self.assertEqual(two['downstream_failed_nodes'], ['C', 'F'])
        self.assertAlmostEqual(two['downstream_failed_demand_share'], 3.0 / 4.0)
        self.assertEqual(two['direction'], 'start_to_end')

    def test_reverse_flow_reports_the_start_node_as_the_head(self):
        eight = effects()['8']
        self.assertEqual(eight['direction'], 'end_to_start')
        self.assertEqual(eight['downstream_failed_nodes'], ['C', 'E', 'F'])
        self.assertTrue(eight['feeds_fault'])

    def test_facility_on_the_supply_path_to_the_fault_is_marked(self):
        rows = effects()
        self.assertTrue(rows['1'] is not None)
        self.assertTrue(rows['4']['feeds_fault'])
        self.assertFalse(rows['3']['feeds_fault'])
        self.assertFalse(rows['6']['feeds_fault'])

    def test_idle_facility_has_no_downstream_effect(self):
        seven = effects()['7']
        self.assertEqual(seven['direction'], 'idle')
        self.assertEqual(seven['downstream_failed_nodes'], [])
        self.assertFalse(seven['feeds_fault'])

    def test_a_facility_is_not_downstream_of_itself(self):
        three = effects()['3']
        self.assertEqual(three['downstream_failed_nodes'], ['C'])
        self.assertNotIn('B', three['downstream_failed_nodes'])

    def test_ordering_puts_the_largest_affected_demand_first_and_idle_last(self):
        rows = facility_effects(LINKS, FLOWS, FAILED, FAULT, ['7', '3', '2', '4', '1'])
        order = [r['link_id'] for r in rows]
        self.assertEqual(order[-1], '7')
        self.assertLess(order.index('2'), order.index('3'))

    def test_equal_reach_is_ordered_by_the_larger_observed_flow(self):
        links = {'a': ('R', 'X'), 'b': ('R', 'Y'), 'c': ('X', 'F'), 'd': ('Y', 'F')}
        rows = facility_effects(links, {'a': 1.0, 'b': 4.0, 'c': 1.0, 'd': 4.0}, {'F': 1.0}, ('F', 'F'), ['a', 'b'])
        self.assertEqual([r['link_id'] for r in rows], ['b', 'a'])

    def test_result_is_deterministic(self):
        first = facility_effects(LINKS, FLOWS, FAILED, FAULT, ['1', '2', '3', '4'])
        second = facility_effects(dict(reversed(list(LINKS.items()))), dict(reversed(list(FLOWS.items()))),
                                  dict(reversed(list(FAILED.items()))), FAULT, ['1', '2', '3', '4'])
        self.assertEqual(first, second)

    def test_unknown_facility_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            facility_effects(LINKS, FLOWS, FAILED, FAULT, ['999'])

    def test_every_row_declares_observation_not_prediction(self):
        for row in effects().values():
            self.assertIn('observed', row['basis'])


if __name__ == '__main__':
    unittest.main()
