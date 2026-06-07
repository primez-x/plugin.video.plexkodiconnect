import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
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

    def getTime(self):
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
    }
    values.update(settings or {})
    properties = {}
    utils = types.ModuleType('resources.lib.utils')
    utils.lang = lambda string_id: {
        30525: 'Skip intro',
        30526: 'Skip credits',
        30530: 'Skip commercial',
    }[string_id]
    utils.settings = lambda key: values[key]
    utils.setGlobalProperty = lambda key, value: properties.__setitem__(key, value)
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
    def test_active_marker_publishes_skin_properties(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(progress=12.0)

        countdown_visible = skip_plex_markers.skip_markers([(10.0, 45.0, 'intro', False)], {})

        self.assertEqual(properties['skip_marker.available'], '1')
        self.assertEqual(properties['skip_marker.type'], 'intro')
        self.assertEqual(properties['skip_marker.label'], 'Skip intro')
        self.assertEqual(properties['skip_marker.end'], '45.0')
        self.assertEqual(properties['skip_marker.toast_visible'], '1')
        self.assertEqual(properties['skip_marker.hide_remaining'], '10')
        self.assertEqual(properties['skip_marker.hide_progress_percent'], '100.0')
        self.assertEqual(properties['skip_marker.hide_progress_frame'], '1000')
        self.assertIs(app.APP.skip_markers_dialog, FakeSkipMarkerDialog.last)
        self.assertTrue(FakeSkipMarkerDialog.last.shown)
        self.assertTrue(countdown_visible)

    def test_auto_hidden_marker_stays_available_to_osd(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(progress=22.0)
        app.APP.skip_markers_dialog = FakeSkipMarkerDialog(creation_time=10.0)
        markers_hidden = {}

        countdown_visible = skip_plex_markers.skip_markers(
            [(10.0, 45.0, 'intro', False)], markers_hidden)

        self.assertEqual(markers_hidden, {'intro': True})
        self.assertIsNone(app.APP.skip_markers_dialog)
        self.assertEqual(properties['skip_marker.available'], '1')
        self.assertEqual(properties['skip_marker.toast_visible'], '')
        self.assertEqual(properties['skip_marker.hide_remaining'], '0')
        self.assertEqual(properties['skip_marker.hide_progress_percent'], '0.0')
        self.assertEqual(properties['skip_marker.hide_progress_frame'], '0')
        self.assertFalse(countdown_visible)

    def test_outside_marker_does_not_request_fast_countdown_polling(self):
        skip_plex_markers, _, properties = load_skip_plex_markers(progress=5.0)

        countdown_visible = skip_plex_markers.skip_markers([(10.0, 45.0, 'intro', False)], {})

        self.assertFalse(countdown_visible)
        self.assertEqual(properties['skip_marker.available'], '')

    def test_visible_countdown_uses_wall_time_when_player_time_is_unchanged(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(progress=12.0)
        ticks = iter([100.0, 100.4])
        skip_plex_markers.time.monotonic = lambda: next(ticks)

        skip_plex_markers.skip_markers([(10.0, 45.0, 'intro', False)], {})
        app.APP.player.progress = 12.0
        countdown_visible = skip_plex_markers.skip_markers(
            [(10.0, 45.0, 'intro', False)], {})

        self.assertTrue(countdown_visible)
        self.assertEqual(properties['skip_marker.hide_remaining'], '10')
        self.assertEqual(properties['skip_marker.hide_progress_percent'], '96.0')
        self.assertEqual(properties['skip_marker.hide_progress_frame'], '960')

    def test_skip_active_marker_seeks_to_published_marker_end(self):
        skip_plex_markers, app, properties = load_skip_plex_markers(progress=15.0)
        properties['skip_marker.end'] = '45.0'

        skip_plex_markers.skip_active_marker()

        self.assertEqual(app.APP.player.seeks, [45.0])


if __name__ == '__main__':
    unittest.main()
