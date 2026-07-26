import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / 'resources'
    / 'lib'
    / 'plex_companion'
    / 'timeline_policy.py'
)
SPEC = importlib.util.spec_from_file_location('timeline_policy', MODULE_PATH)
timeline_policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(timeline_policy)


class TimelinePolicyTests(unittest.TestCase):
    def test_first_pause_is_reported(self):
        self.assertTrue(timeline_policy.should_send_pms_timeline(
            {'state': 'playing', 'ratingKey': '100'},
            {'state': 'paused', 'ratingKey': '100'},
        ))

    def test_repeated_pause_for_same_item_is_suppressed(self):
        self.assertFalse(timeline_policy.should_send_pms_timeline(
            {'state': 'paused', 'ratingKey': '100', 'time': '1000'},
            {'state': 'paused', 'ratingKey': '100', 'time': '9000'},
        ))

    def test_pause_for_new_item_is_reported(self):
        self.assertTrue(timeline_policy.should_send_pms_timeline(
            {'state': 'paused', 'ratingKey': '100'},
            {'state': 'paused', 'ratingKey': '101'},
        ))

    def test_resume_and_stop_are_reported(self):
        previous = {'state': 'paused', 'ratingKey': '100'}
        self.assertTrue(timeline_policy.should_send_pms_timeline(
            previous, {'state': 'playing', 'ratingKey': '100'}))
        self.assertTrue(timeline_policy.should_send_pms_timeline(
            previous, {'state': 'stopped', 'ratingKey': '100'}))

    def test_failed_resumed_send_rearms_the_next_pause(self):
        state = timeline_policy.PauseTimelineState()
        paused = {'state': 'paused', 'ratingKey': '100', 'time': '1000'}
        state.record_success(paused)
        self.assertTrue(state.suppresses(paused))

        # Kodi exposes a resume, but its following HTTP request fails. The
        # state must still be re-armed before that I/O so the later pause is
        # not suppressed behind the old successful paused position.
        self.assertEqual(
            state.observe({'state': 'playing', 'ratingKey': '100'}),
            '100',
        )
        self.assertFalse(
            state.suppresses({'state': 'paused', 'ratingKey': '100', 'time': '9000'})
        )

    def test_failed_first_pause_is_retried_until_one_succeeds(self):
        state = timeline_policy.PauseTimelineState()
        paused = {'state': 'paused', 'ratingKey': '100'}

        self.assertFalse(state.suppresses(paused))
        # No record_success() models a failed HTTP request.
        self.assertFalse(state.suppresses(paused))
        state.record_success(paused)
        self.assertTrue(state.suppresses(paused))

    def test_successful_resume_rearms_the_next_pause_normally(self):
        state = timeline_policy.PauseTimelineState()
        paused = {'state': 'paused', 'ratingKey': '100'}
        state.record_success(paused)

        state.observe({'state': 'playing', 'ratingKey': '100'})
        self.assertFalse(state.suppresses(paused))
        state.record_success(paused)
        self.assertTrue(state.suppresses(paused))


if __name__ == '__main__':
    unittest.main()
