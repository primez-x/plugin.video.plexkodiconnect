import importlib
import sys
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_MONOTONIC = time.monotonic
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class DummyLock(object):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakePlayer(object):
    def __init__(self, progress):
        self.progress = progress
        self.seeks = []
        self.get_time_calls = 0

    def getTime(self):
        self.get_time_calls += 1
        return self.progress

    def seekTime(self, value):
        self.seeks.append(value)


class FakeSkipMarkerDialog(object):
    last = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.creation_time = kwargs.get('creation_time')
        self.creation_walltime = kwargs.get('creation_walltime')
        self.closed = False
        self.shown = False
        self.seeked = False
        FakeSkipMarkerDialog.last = self

    def seekTimeToEnd(self):
        self.seeked = True

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True


def load_skip_plex_markers(progress, settings=None):
    for module_name in (
        'xbmc',
        'resources.lib.app',
        'resources.lib.utils',
        'resources.lib.variables',
        'resources.lib.windows.skip_marker',
        'resources.lib.skip_marker_state',
        'resources.lib.skip_plex_markers',
    ):
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType('xbmc')
    xbmc.getCondVisibility = lambda condition: False
    sys.modules['xbmc'] = xbmc

    app = types.ModuleType('resources.lib.app')
    app.APP = SimpleNamespace(
        player=FakePlayer(progress),
        skip_markers_dialog=None,
        is_playing=True,
        lock_playqueues=DummyLock(),
    )
    app.PLAYSTATE = SimpleNamespace(active_players=set(), player_states={})
    sys.modules['resources.lib.app'] = app

    values = {
        'enableSkipIntro': 'true',
        'enableAutoSkipIntro': 'false',
        'enableAutoHideSkip': 'true',
        'enableAutoHideSkipTime': '10',
        'skipIntroStartOffset': '0',
        'skipIntroEndOffset': '0',
    }
    values.update(settings or {})
    properties = {}
    property_writes = []
    setting_calls = {}
    utils = types.ModuleType('resources.lib.utils')
    utils.lang = lambda string_id: {
        30525: 'Skip intro',
        30526: 'Skip credits',
        30530: 'Skip commercial',
        39729: 'Skipping in...',
    }[string_id]
    def settings(key):
        setting_calls[key] = setting_calls.get(key, 0) + 1
        return values[key]

    def set_global_property(key, value):
        property_writes.append((key, value))
        properties[key] = value

    utils.settings = settings
    utils.setting_calls = setting_calls
    utils.property_writes = property_writes
    utils.setGlobalProperty = set_global_property
    utils.getGlobalProperty = lambda key: properties.get(key, '')
    sys.modules['resources.lib.utils'] = utils

    variables = types.ModuleType('resources.lib.variables')
    variables.ADDON_PATH = 'special://home/addons/plugin.video.plexkodiconnect'
    sys.modules['resources.lib.variables'] = variables

    window_module = types.ModuleType('resources.lib.windows.skip_marker')
    window_module.SkipMarkerDialog = FakeSkipMarkerDialog
    sys.modules['resources.lib.windows.skip_marker'] = window_module

    module = importlib.import_module('resources.lib.skip_plex_markers')
    return module, app, properties


class SkipPlexMarkersTests(unittest.TestCase):
    def test_credit_popup_is_suppressed_only_for_marker_anchored_upnext(self):
        skip_plex_markers, app, _ = load_skip_plex_markers(progress=12.0)
        skip_plex_markers.xbmc.getCondVisibility = lambda condition: True
        app.PLAYSTATE.active_players = {1}
        app.PLAYSTATE.player_states = {
            1: {
                'upnext_signal_sent': True,
                'upnext_replaces_credit_skip': True,
            },
        }

        self.assertTrue(skip_plex_markers._should_skip_credits_popup())

    def test_native_upnext_signal_does_not_hide_credit_popup(self):
        skip_plex_markers, app, _ = load_skip_plex_markers(progress=12.0)
        skip_plex_markers.xbmc.getCondVisibility = lambda condition: True
        app.PLAYSTATE.active_players = {1}
        app.PLAYSTATE.player_states = {
            1: {
                'upnext_signal_sent': True,
                'upnext_replaces_credit_skip': False,
            },
        }

        self.assertFalse(skip_plex_markers._should_skip_credits_popup())

    def test_active_marker_publishes_skin_properties(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(
            progress=12.0,
            settings={'enableAutoSkipIntro': 'true'})

        countdown_visible = skip_plex_markers.skip_markers(
            [(20.0, 45.0, 'intro', False)], {})

        self.assertEqual(properties['skip_marker.available'], '1')
        self.assertEqual(properties['skip_marker.type'], 'intro')
        self.assertEqual(properties['skip_marker.label'], 'Skip intro')
        self.assertEqual(properties['skip_marker.end'], '45.0')
        self.assertEqual(properties['skip_marker.toast_visible'], '1')
        self.assertEqual(properties['skip_marker.hide_remaining'], '8')
        self.assertEqual(properties['skip_marker.hide_progress_percent'], '80.0')
        self.assertEqual(properties['skip_marker.hide_progress_frame'], '800')
        self.assertIs(app.APP.skip_markers_dialog, FakeSkipMarkerDialog.last)
        self.assertTrue(FakeSkipMarkerDialog.last.shown)
        self.assertTrue(countdown_visible)

    def test_auto_hidden_marker_stays_available_to_osd(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(
            progress=12.0,
            settings={'enableAutoSkipIntro': 'true'})
        app.APP.skip_markers_dialog = FakeSkipMarkerDialog(creation_time=10.0)
        app.APP.skip_markers_dialog.on_hold = True
        markers_hidden = {}

        countdown_visible = skip_plex_markers.skip_markers(
            [(20.0, 45.0, 'intro', False)], markers_hidden)

        self.assertEqual(markers_hidden, {'intro': True})
        self.assertIsNone(app.APP.skip_markers_dialog)
        self.assertEqual(properties['skip_marker.available'], '1')
        self.assertEqual(properties['skip_marker.toast_visible'], '')
        self.assertEqual(properties['skip_marker.hide_remaining'], '10')
        self.assertEqual(properties['skip_marker.hide_progress_percent'], '100.0')
        self.assertEqual(properties['skip_marker.hide_progress_frame'], '1000')
        self.assertFalse(countdown_visible)

    def test_outside_marker_does_not_request_fast_countdown_polling(self):
        skip_plex_markers, _, properties = load_skip_plex_markers(progress=5.0)

        countdown_visible = skip_plex_markers.skip_markers([(10.0, 45.0, 'intro', False)], {})

        self.assertFalse(countdown_visible)
        self.assertEqual(properties['skip_marker.available'], '')

    def test_visible_countdown_stays_stable_when_player_time_is_unchanged(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(
            progress=12.0,
            settings={'enableAutoSkipIntro': 'true'})
        ticks = iter([100.0, 100.4])
        skip_plex_markers.time.monotonic = lambda: next(ticks)
        try:
            skip_plex_markers.skip_markers([(20.0, 45.0, 'intro', False)], {})
            app.APP.player.progress = 12.0
            countdown_visible = skip_plex_markers.skip_markers(
                [(20.0, 45.0, 'intro', False)], {})

            self.assertTrue(countdown_visible)
            self.assertEqual(properties['skip_marker.hide_remaining'], '8')
            self.assertEqual(properties['skip_marker.hide_progress_percent'], '80.0')
            self.assertEqual(properties['skip_marker.hide_progress_frame'], '800')
        finally:
            skip_plex_markers.time.monotonic = ORIGINAL_MONOTONIC

    def test_check_revisits_a_visible_countdown_at_33ms(self):
        skip_plex_markers, app, _ = load_skip_plex_markers(
            progress=12.0,
            settings={'enableAutoSkipIntro': 'true'})
        app.PLAYSTATE.active_players = {1}
        app.PLAYSTATE.player_states = {
            1: {
                'markers': [(20.0, 45.0, 'intro', False)],
                'markers_hidden': {},
                'speed': 1,
            },
        }
        ticks = iter([
            100.000, 100.000, 100.033,
            100.067, 100.067, 100.100,
        ])
        skip_plex_markers.time.monotonic = lambda: next(ticks)
        try:
            self.assertTrue(skip_plex_markers.check())
            self.assertEqual(app.APP.player.get_time_calls, 1)

            self.assertTrue(skip_plex_markers.check())
            self.assertEqual(app.APP.player.get_time_calls, 2)
        finally:
            skip_plex_markers.time.monotonic = ORIGINAL_MONOTONIC

    def test_distant_marker_is_deadline_gated(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(progress=5.0)
        app.PLAYSTATE.active_players = {1}
        app.PLAYSTATE.player_states = {
            1: {
                'markers': [(3600.0, 3645.0, 'intro', False)],
                'markers_hidden': {},
                'speed': 1,
            },
        }
        player = app.APP.player
        utils = sys.modules['resources.lib.utils']

        self.assertFalse(skip_plex_markers.check())
        property_write_count = len(utils.setting_calls)
        property_set_count = len(properties)

        for _ in range(20):
            self.assertFalse(skip_plex_markers.check())

        self.assertEqual(player.get_time_calls, 1)
        self.assertEqual(len(utils.setting_calls), property_write_count)
        self.assertEqual(len(properties), property_set_count)

    def test_marker_properties_are_diff_written(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(
            progress=12.0,
            settings={'enableAutoSkipIntro': 'true'})
        utils = sys.modules['resources.lib.utils']

        self.assertTrue(
            skip_plex_markers.skip_markers(
                [(20.0, 45.0, 'intro', False)], {}, progress=12.0))
        writes_after_first_publish = len(utils.setting_calls)
        property_writes = len(utils.property_writes)

        self.assertTrue(
            skip_plex_markers.skip_markers(
                [(20.0, 45.0, 'intro', False)], {}, progress=12.0))

        self.assertEqual(len(utils.setting_calls), writes_after_first_publish)
        self.assertEqual(len(utils.property_writes), property_writes)
        self.assertEqual(properties['skip_marker.hide_progress_frame'], '800')

    def test_manual_marker_does_not_request_fast_countdown_polling(self):
        skip_plex_markers, app, _ = load_skip_plex_markers(progress=20.0)
        app.PLAYSTATE.active_players = {1}
        app.PLAYSTATE.player_states = {
            1: {
                'markers': [(20.0, 45.0, 'intro', False)],
                'markers_hidden': {},
                'speed': 1,
            },
        }

        self.assertFalse(skip_plex_markers.check())

    def test_runtime_reset_forces_the_next_distant_marker_evaluation(self):
        skip_plex_markers, app, _ = load_skip_plex_markers(progress=5.0)
        app.PLAYSTATE.active_players = {1}
        app.PLAYSTATE.player_states = {
            1: {
                'markers': [(3600.0, 3645.0, 'intro', False)],
                'markers_hidden': {},
                'speed': 1,
            },
        }
        player = app.APP.player

        skip_plex_markers.check()
        self.assertEqual(player.get_time_calls, 1)
        skip_plex_markers.reset_runtime()
        skip_plex_markers.check()
        self.assertEqual(player.get_time_calls, 2)

    def test_skip_active_marker_seeks_to_published_marker_end(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(progress=15.0)
        properties['skip_marker.end'] = '45.0'

        skip_plex_markers.skip_active_marker()

        self.assertEqual(app.APP.player.seeks, [45.0])


if __name__ == '__main__':
    unittest.main()
