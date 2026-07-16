import unittest

from atomic_problem_core import load_atomic_instance, validate_event_constancy


class AtomicBoundaryEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instance = load_atomic_instance("data/instances/atomic_l5_l1/instance_w30_atomic.json")

    def test_events_are_feasible_unique_and_constant(self):
        for events in (self.instance.upper_events, self.instance.lower_events):
            self.assertEqual([e.event_index for e in events], list(range(len(events))))
            self.assertEqual(len({e.assignment_hash for e in events}), len(events))
            self.assertTrue(all(validate_event_constancy(e, self.instance.atomic_welds) for e in events))

    def test_no_event_boundary_strictly_crosses_an_atomic_weld(self):
        for event in (*self.instance.upper_events, *self.instance.lower_events):
            for weld in self.instance.atomic_welds:
                if weld.half_region == event.half_region:
                    self.assertFalse(min(weld.start[0], weld.end[0]) < event.representative_x < max(weld.start[0], weld.end[0]))

    def test_each_event_assigns_every_half_task_once(self):
        for event in (*self.instance.upper_events, *self.instance.lower_events):
            expected = {w.id for w in self.instance.atomic_welds if w.half_region == event.half_region}
            self.assertEqual(set(event.left_atomic_ids).union(event.right_atomic_ids), expected)
            self.assertFalse(set(event.left_atomic_ids).intersection(event.right_atomic_ids))


if __name__ == "__main__": unittest.main()
