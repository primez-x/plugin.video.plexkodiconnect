import importlib.util
import unittest
from threading import Event
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



def playing(time_ms, rating_key='100', **extra):
    timeline = {'state': 'playing', 'ratingKey': rating_key,
                'key': '/library/metadata/%s' % rating_key,
                'time': str(time_ms)}
    timeline.update(extra)
    return timeline


class PlayingTimelineThrottleTests(unittest.TestCase):
    def setUp(self):
        self.throttle = timeline_policy.PlayingTimelineThrottle()
        self.throttle.record_success(playing(60000), now=1000.0)

    def test_steady_playback_is_reported_every_interval(self):
        for second in range(1, 10):
            self.assertFalse(self.throttle.due(
                playing(60000 + second * 1000), now=1000.0 + second))
        self.assertTrue(self.throttle.due(playing(70000), now=1010.0))

    def test_companion_subscribers_keep_the_one_second_cadence(self):
        self.assertTrue(self.throttle.due(
            playing(61000), now=1001.0, frequent=True))

    def test_seek_item_change_and_state_change_are_reported_at_once(self):
        self.assertTrue(self.throttle.due(playing(300000), now=1002.0))
        self.assertTrue(self.throttle.due(playing(10000), now=1002.0))
        self.assertTrue(self.throttle.due(
            playing(62000, rating_key='101'), now=1002.0))
        self.assertTrue(self.throttle.due(
            playing(62000, playQueueItemID='7'), now=1002.0))
        paused = dict(playing(62000), state='paused')
        self.assertTrue(self.throttle.due(paused, now=1002.0))
        stopped = dict(playing(62000), state='stopped')
        self.assertTrue(self.throttle.due(stopped, now=1002.0))

    def test_nothing_reported_yet_or_after_reset_is_sent(self):
        self.throttle.reset()
        self.assertTrue(self.throttle.due(playing(61000), now=1001.0))
        fresh = timeline_policy.PlayingTimelineThrottle()
        self.assertTrue(fresh.due(playing(0), now=5.0))


class PlaylistPollTests(unittest.TestCase):
    def test_playback_polls_every_tick(self):
        changed = Event()
        self.assertTrue(timeline_policy.playlist_poll_due(
            changed, idle=False, last_poll=99.5, now=100.0))

    def test_idle_polls_only_after_a_change_or_the_slow_fallback(self):
        changed = Event()
        self.assertFalse(timeline_policy.playlist_poll_due(
            changed, idle=True, last_poll=99.0, now=100.0))
        changed.set()
        self.assertTrue(timeline_policy.playlist_poll_due(
            changed, idle=True, last_poll=99.0, now=100.0))
        # Consumed before reading the playlists
        self.assertFalse(changed.is_set())
        self.assertFalse(timeline_policy.playlist_poll_due(
            changed, idle=True, last_poll=99.0, now=103.0))
        self.assertTrue(timeline_policy.playlist_poll_due(
            changed, idle=True, last_poll=99.0,
            now=99.0 + timeline_policy.IDLE_PLAYLIST_POLL_INTERVAL))

    def test_first_poll_and_missing_signal_fall_back_to_polling(self):
        self.assertTrue(timeline_policy.playlist_poll_due(
            Event(), idle=True, last_poll=None, now=100.0))
        self.assertTrue(timeline_policy.playlist_poll_due(
            None, idle=True, last_poll=99.9, now=100.0))


if __name__ == '__main__':
    unittest.main()
