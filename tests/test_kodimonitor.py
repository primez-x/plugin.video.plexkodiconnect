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


def load_kodimonitor():
    for module_name in (
        'xbmc',
        'resources.lib.kodimonitor',
        'resources.lib.plex_api',
        'resources.lib.plex_db',
        'resources.lib.kodi_db',
        'resources.lib.downloadutils',
        'resources.lib.utils',
        'resources.lib.timing',
        'resources.lib.plex_functions',
        'resources.lib.json_rpc',
        'resources.lib.playlist_func',
        'resources.lib.backgroundthread',
        'resources.lib.app',
        'resources.lib.variables',
        'resources.lib.exceptions',
        'resources.lib.skip_marker_state',
        'resources.lib.upnext',
    ):
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType('xbmc')
    xbmc.commands = []
    xbmc.executebuiltin = lambda command: xbmc.commands.append(command)

    class Monitor(object):
        def __init__(self):
            pass

        def waitForAbort(self, timeout):
            return False

    class Player(object):
        def __init__(self):
            pass

    xbmc.Monitor = Monitor
    xbmc.Player = Player
    sys.modules['xbmc'] = xbmc

    plex_api = types.ModuleType('resources.lib.plex_api')
    plex_api.API = object

    plex_db = types.ModuleType('resources.lib.plex_db')
    plex_db.PlexDB = object

    kodi_db = types.ModuleType('resources.lib.kodi_db')
    kodi_db.KodiVideoDB = object

    downloadutils = types.ModuleType('resources.lib.downloadutils')
    downloadutils.DownloadUtils = object

    utils = types.ModuleType('resources.lib.utils')
    utils.setGlobalProperty = lambda *args, **kwargs: None
    utils.delete_temporary_subtitles = lambda: None

    timing = types.ModuleType('resources.lib.timing')

    json_rpc = types.ModuleType('resources.lib.json_rpc')
    json_rpc.players = {}
    json_rpc.current_window = {'id': 10000, 'label': 'Home'}
    json_rpc.get_players = lambda: json_rpc.players

    class FakeJsonRPC(object):
        def __init__(self, method):
            self.method = method

        def execute(self, params=None):
            if self.method == 'GUI.GetProperties':
                return {'result': {'currentwindow': json_rpc.current_window}}
            raise AssertionError('Unexpected JSON-RPC method: %s' % self.method)

    json_rpc.JsonRPC = FakeJsonRPC

    playlist_func = types.ModuleType('resources.lib.playlist_func')

    backgroundthread = types.ModuleType('resources.lib.backgroundthread')

    class Task(object):
        def __init__(self, priority=None):
            self.priority = priority
            self.finished = False

    class BGThreader(object):
        tasks = []

        @classmethod
        def addTask(cls, task):
            cls.tasks.append(task)

    backgroundthread.Task = Task
    backgroundthread.BGThreader = BGThreader

    app = types.ModuleType('resources.lib.app')
    app.PLAYSTATE = SimpleNamespace(
        active_players={2},
        player_states={2: {'playmethod': None}},
        template={'playmethod': None},
        item='playing item',
    )
    app.APP = SimpleNamespace(
        skip_markers_dialog=None,
        lock_playqueues=DummyLock(),
        monitor=SimpleNamespace(
            abortRequested=lambda: False,
            waitForAbort=lambda timeout: False,
        ),
    )
    app.CONN = SimpleNamespace(plex_transient_token='token')

    variables = types.ModuleType('resources.lib.variables')
    variables.PLAYBACK_METHOD_TRANSCODE = 3
    variables.PLEX_VIDEOTYPES = ('movie', 'episode', 'clip')

    exceptions = types.ModuleType('resources.lib.exceptions')

    skip_marker_state = types.ModuleType('resources.lib.skip_marker_state')
    skip_marker_state.clear_properties = lambda: {}

    upnext = types.ModuleType('resources.lib.upnext')

    for module_name, module in {
        'resources.lib.plex_api': plex_api,
        'resources.lib.plex_db': plex_db,
        'resources.lib.kodi_db': kodi_db,
        'resources.lib.downloadutils': downloadutils,
        'resources.lib.utils': utils,
        'resources.lib.timing': timing,
        'resources.lib.plex_functions': types.ModuleType('resources.lib.plex_functions'),
        'resources.lib.json_rpc': json_rpc,
        'resources.lib.playlist_func': playlist_func,
        'resources.lib.backgroundthread': backgroundthread,
        'resources.lib.app': app,
        'resources.lib.variables': variables,
        'resources.lib.exceptions': exceptions,
        'resources.lib.skip_marker_state': skip_marker_state,
        'resources.lib.upnext': upnext,
    }.items():
        sys.modules[module_name] = module

    return importlib.import_module('resources.lib.kodimonitor'), xbmc, json_rpc, backgroundthread


class KodiMonitorTests(unittest.TestCase):
    def test_upnext_marker_timing_replaces_credit_skip(self):
        kodimonitor, _, _, _ = load_kodimonitor()
        item = SimpleNamespace(playerid=2, api='api')
        kodimonitor.upnext.get_notification_time_from_markers = \
            lambda status: 88.205
        kodimonitor.upnext.send_upnext_signal = \
            lambda api, notification_time: True

        kodimonitor.SendUpNextSignal(item, {'totaltime': {}}).run()

        state = kodimonitor.app.PLAYSTATE.player_states[2]
        self.assertTrue(state['upnext_signal_sent'])
        self.assertTrue(state['upnext_replaces_credit_skip'])

    def test_native_upnext_fallback_does_not_replace_credit_skip(self):
        kodimonitor, _, _, _ = load_kodimonitor()
        item = SimpleNamespace(playerid=2, api='api')
        kodimonitor.upnext.get_notification_time_from_markers = \
            lambda status: None
        kodimonitor.upnext.send_upnext_signal = \
            lambda api, notification_time: True

        kodimonitor.SendUpNextSignal(item, {'totaltime': {}}).run()

        state = kodimonitor.app.PLAYSTATE.player_states[2]
        self.assertTrue(state['upnext_signal_sent'])
        self.assertFalse(state['upnext_replaces_credit_skip'])

    def test_playback_cleanup_schedules_stranded_window_recovery(self):
        kodimonitor, _, _, backgroundthread = load_kodimonitor()

        kodimonitor._playback_cleanup()

        self.assertEqual(len(backgroundthread.BGThreader.tasks), 1)
        self.assertEqual(
            backgroundthread.BGThreader.tasks[0].__class__.__name__,
            'RecoverStrandedPlaybackWindow',
        )

    def test_recovery_closes_playback_window_when_no_players_remain(self):
        kodimonitor, xbmc, json_rpc, _ = load_kodimonitor()
        json_rpc.players = {}
        json_rpc.current_window = {'id': 12901, 'label': 'Fullscreen OSD'}
        kodimonitor.LOG.warning = lambda *args, **kwargs: None

        kodimonitor.RecoverStrandedPlaybackWindow(delay=0).run()

        self.assertEqual(xbmc.commands, [
            'Dialog.Close(busydialog, true)',
            'Dialog.Close(videoosd, true)',
            'Dialog.Close(fullscreeninfo, true)',
            'ActivateWindow(Home)',
        ])

    def test_recovery_skips_when_a_player_is_still_active(self):
        kodimonitor, xbmc, json_rpc, _ = load_kodimonitor()
        json_rpc.players = {'video': {'playerid': 1, 'type': 'video'}}
        json_rpc.current_window = {'id': 12901, 'label': 'Fullscreen OSD'}

        kodimonitor.RecoverStrandedPlaybackWindow(delay=0).run()

        self.assertEqual(xbmc.commands, [])

    def test_recovery_skips_non_playback_windows(self):
        kodimonitor, xbmc, json_rpc, _ = load_kodimonitor()
        json_rpc.players = {}
        json_rpc.current_window = {'id': 10000, 'label': 'Home'}

        kodimonitor.RecoverStrandedPlaybackWindow(delay=0).run()

        self.assertEqual(xbmc.commands, [])


if __name__ == '__main__':
    unittest.main()
