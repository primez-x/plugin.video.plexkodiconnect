import importlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from threading import Event
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
        'resources.lib.skip_plex_markers',
        'resources.lib.skip_marker_state',
        'resources.lib.upnext',
        'resources.lib.gui_refresh',
    ):
        sys.modules.pop(module_name, None)
    lib_package = sys.modules.get('resources.lib')
    if lib_package is not None:
        lib_package.__dict__.pop('skip_plex_markers', None)
        lib_package.__dict__.pop('gui_refresh', None)

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
    timing.kodi_time_to_millis = lambda value: (
        (value['hours'] * 3600 +
         value['minutes'] * 60 +
         value['seconds']) * 1000 +
        value['milliseconds'])
    timing.kodi_now = lambda: '2026-08-02 09:00:00'

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

        @classmethod
        def addTasksToFront(cls, tasks):
            cls.tasks.extend(tasks)

    class FunctionAsTask(Task):
        def __init__(self, function, callback, *args, **kwargs):
            super(FunctionAsTask, self).__init__()
            self.function = function
            self.callback = callback
            self.args = args
            self.kwargs = kwargs

    backgroundthread.Task = Task
    backgroundthread.BGThreader = BGThreader
    backgroundthread.FunctionAsTask = FunctionAsTask

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
        discover_refresh_event=Event(),
        discover_maintenance_event=Event(),
        monitor=SimpleNamespace(
            abortRequested=lambda: False,
            waitForAbort=lambda timeout: False,
        ),
    )
    app.CONN = SimpleNamespace(plex_transient_token='token')

    variables = types.ModuleType('resources.lib.variables')
    variables.PLAYBACK_METHOD_TRANSCODE = 3
    variables.PLEX_VIDEOTYPES = ('movie', 'episode', 'clip')
    variables.PLEX_TYPE_EPISODE = 'episode'
    variables.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR = 0
    variables.MARK_PLAYED_AT = 0.90
    variables.KODI_PLAYCOUNTMINIMUMPERCENT = 0.80
    variables.KODI_IGNORESECONDSATSTART = 10
    variables.KODI_IGNOREPERCENTATEND = 8
    variables.IGNORE_SECONDS_AT_START = 5

    exceptions = types.ModuleType('resources.lib.exceptions')

    skip_marker_state = types.ModuleType('resources.lib.skip_marker_state')
    skip_marker_state.clear_properties = lambda: {}

    skip_plex_markers = types.ModuleType('resources.lib.skip_plex_markers')
    skip_plex_markers.reset_calls = []
    skip_plex_markers.reset_runtime = lambda clear_properties=False: \
        skip_plex_markers.reset_calls.append(clear_properties)

    upnext = types.ModuleType('resources.lib.upnext')

    gui_refresh = types.ModuleType('resources.lib.gui_refresh')
    gui_refresh.calls = []
    gui_refresh.refresh_sidepanel_containers = \
        lambda items: gui_refresh.calls.append(tuple(items))

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
        'resources.lib.skip_plex_markers': skip_plex_markers,
        'resources.lib.upnext': upnext,
        'resources.lib.gui_refresh': gui_refresh,
    }.items():
        sys.modules[module_name] = module

    return importlib.import_module('resources.lib.kodimonitor'), xbmc, json_rpc, backgroundthread


def kodi_time(seconds):
    milliseconds = int(seconds * 1000)
    return {
        'hours': milliseconds // 3600000,
        'minutes': milliseconds // 60000 % 60,
        'seconds': milliseconds // 1000 % 60,
        'milliseconds': milliseconds % 1000,
    }


def playback_status(plex_type='episode', position_seconds=1020,
                    total_seconds=1200):
    return {
        'plex_id': '16388',
        'plex_type': plex_type,
        'playcount': 0,
        'external_player': False,
        'time': kodi_time(position_seconds),
        'totaltime': kodi_time(total_seconds),
        'first_credits_marker': None,
        'final_credits_marker': None,
    }


def install_playstate_databases(kodimonitor):
    writes = []

    class FakePlexDB(object):
        def __init__(self, lock=False):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def item_by_id(self, plex_id, plex_type):
            return {'plex_id': plex_id, 'kodi_fileid': 42}

    class FakeKodiVideoDB(object):
        def __init__(self, lock=True):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def set_resume(self, *args):
            writes.append(args)

    kodimonitor.PlexDB = FakePlexDB
    kodimonitor.kodi_db.KodiVideoDB = FakeKodiVideoDB
    return writes


def install_onupdate_databases(kodimonitor, playcount=0):
    writes = []

    class FakePlexDB(object):
        def __init__(self, lock=False):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def item_by_kodi_id(self, kodi_id, kodi_type):
            return {
                'plex_id': '16390',
                'plex_type': 'episode',
                'kodi_fileid': 42,
                'kodi_fileid_2': 43,
            }

    class FakeKodiVideoDB(object):
        def __init__(self, lock=False):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def file_id_from_id(self, kodi_id, kodi_type):
            return 42

        def get_resume(self, file_id):
            return None

        def get_playcount(self, file_id):
            return playcount

        def set_resume(self, *args):
            writes.append(args)

    kodimonitor.PlexDB = FakePlexDB
    kodimonitor.KodiVideoDB = FakeKodiVideoDB
    kodimonitor.kodi_db.KodiVideoDB = FakeKodiVideoDB
    return writes


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

    def test_discover_notifications_wake_the_matching_service_event(self):
        kodimonitor, _, _, _ = load_kodimonitor()
        monitor = kodimonitor.KodiMonitor()

        monitor.onNotification(
            'plugin.video.plexkodiconnect.SIGNAL',
            'discover_cache_refresh',
            '')
        self.assertTrue(kodimonitor.app.APP.discover_refresh_event.is_set())
        self.assertFalse(kodimonitor.app.APP.discover_maintenance_event.is_set())

        kodimonitor.app.APP.discover_refresh_event.clear()
        monitor.onNotification(
            'plugin.video.plexkodiconnect.SIGNAL',
            'discover_cache_maintenance',
            '')
        self.assertTrue(kodimonitor.app.APP.discover_maintenance_event.is_set())

    def test_playback_and_settings_transitions_invalidate_marker_schedule(self):
        kodimonitor, _, _, _ = load_kodimonitor()
        monitor = kodimonitor.KodiMonitor()

        monitor.onSettingsChanged()
        monitor.onNotification('sender', 'Player.OnSeek', '')
        monitor.onNotification('sender', 'Player.OnPause', '')
        monitor.onNotification('sender', 'Player.OnResume', '')

        self.assertEqual(
            kodimonitor.skip_plex_markers.reset_calls,
            [False, False, False, False])

    def test_playback_cleanup_schedules_stranded_window_recovery(self):
        kodimonitor, _, _, backgroundthread = load_kodimonitor()

        kodimonitor._playback_cleanup()

        self.assertEqual(len(backgroundthread.BGThreader.tasks), 1)
        self.assertEqual(
            backgroundthread.BGThreader.tasks[0].__class__.__name__,
            'RecoverStrandedPlaybackWindow',
        )

    def test_manual_watch_update_propagates_during_unrelated_transition(self):
        kodimonitor, _, _, backgroundthread = load_kodimonitor()
        install_onupdate_databases(kodimonitor)
        kodimonitor.PF.scrobble = lambda plex_id, state: None
        kodimonitor.app.PLAYSTATE.item = None
        old_item = SimpleNamespace(
            kodi_id=8950,
            kodi_type='episode',
            plex_id='16390',
            pkc_playback_generation=4,
        )
        new_item = SimpleNamespace(
            kodi_id=8951,
            kodi_type='episode',
            plex_id='16391',
        )
        self.assertFalse(kodimonitor._activate_upnext_handoff(
            old_item, new_item, now=100.0))
        kodimonitor.monotonic = lambda: 101.0

        kodimonitor._videolibrary_onupdate({
            'item': {'id': 8950, 'type': 'episode'},
            'playcount': 1,
        })

        scrobbles = [
            task for task in backgroundthread.BGThreader.tasks
            if isinstance(task, backgroundthread.FunctionAsTask)
            and task.function is kodimonitor.PF.scrobble
        ]
        self.assertEqual(len(scrobbles), 1)
        self.assertEqual(scrobbles[0].args, ('16390', 'watched'))

    def test_verified_up_next_ignores_delayed_update_beyond_five_seconds(self):
        kodimonitor, xbmc, json_rpc, backgroundthread = load_kodimonitor()
        writes = install_onupdate_databases(kodimonitor)
        kodimonitor.PF.scrobble = lambda plex_id, state: None
        kodimonitor.PF.GetPlexMetadata = lambda plex_id: [ET.Element(
            'Video',
            viewCount='1',
            viewOffset='0',
            duration='1434000',
            lastViewedAt='1785892781',
        )]
        kodimonitor.API = lambda xml: SimpleNamespace(
            resume_point=lambda: 0.0,
            runtime=lambda: 1434.0,
            viewcount=lambda: 1,
            lastplayed=lambda: '2026-08-04 19:19:41',
        )
        kodimonitor.utils.settings = lambda setting_id: 'false'
        xbmc.getCondVisibility = lambda condition: False
        kodimonitor.v.KODI_VIDEO_PLAYER_ID = 99
        kodimonitor.app.APP.player = SimpleNamespace(
            isExternalPlayer=lambda: 0)
        old_item = SimpleNamespace(
            kodi_id=8950,
            kodi_type='episode',
            plex_id='16390',
            pkc_playback_generation=4,
        )
        new_item = SimpleNamespace(
            kodi_id=8951,
            kodi_type='episode',
            plex_id='16391',
            plex_type='episode',
            file='plugin://plugin.video.plexkodiconnect/?plex_id=16391',
            playmethod=1,
            playcount=0,
            playerid=None,
        )
        kodimonitor.app.PLAYSTATE.expected_upnext_handoff = {
            'token': 'verified-token',
            'previous_kodi_id': 8950,
            'previous_kodi_type': 'episode',
            'previous_plex_id': '16390',
            'previous_generation': 4,
            'next_plex_id': '16391',
            'created_at': 100.0,
        }
        kodimonitor.app.PLAYSTATE.started_upnext_handoff = {
            'token': 'verified-token',
            'plex_id': '16391',
            'created_at': 100.0,
        }
        kodimonitor.monotonic = lambda: 100.0
        kodimonitor.app.PLAYSTATE.item = old_item
        kodimonitor.app.PLAYSTATE.active_players = set()
        kodimonitor.app.PLAYSTATE.template = {'playmethod': None}
        kodimonitor.app.PLAYSTATE.player_states = {
            1: dict(kodimonitor.app.PLAYSTATE.template),
        }
        kodimonitor.app.PLAYQUEUES = {
            1: SimpleNamespace(
                items=[new_item],
                kodi_playlist_playback=False,
                id=None,
            ),
        }
        json_rpc.get_player_props = lambda playerid: {
            'position': 0,
            'playlistid': -1,
        }

        monitor = kodimonitor.KodiMonitor()
        monitor.PlayBackStart({
            'item': {
                'id': 8951,
                'type': 'episode',
                'file': new_item.file,
            },
            'player': {'playerid': 1, 'speed': 1},
        })
        kodimonitor.monotonic = lambda: 106.0
        kodimonitor._videolibrary_onupdate({
            'item': {'id': 8950, 'type': 'episode'},
            'playcount': 0,
        })
        kodimonitor._videolibrary_onupdate({
            'id': 8950,
            'type': 'episode',
        })

        scrobbles = [
            task for task in backgroundthread.BGThreader.tasks
            if isinstance(task, backgroundthread.FunctionAsTask)
            and task.function is kodimonitor.PF.scrobble
        ]
        self.assertEqual(scrobbles, [])
        restores = [
            task for task in backgroundthread.BGThreader.tasks
            if isinstance(task, kodimonitor.RestorePlexPlaystate)
        ]
        self.assertEqual(len(restores), 1)

        restores[0].run()

        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0][:4], (42, 0.0, 1434.0, 1))
        self.assertEqual(writes[1][:4], (43, 0.0, 1434.0, 1))

    def test_rapid_return_does_not_reuse_stale_handoff(self):
        kodimonitor, _, _, backgroundthread = load_kodimonitor()
        install_onupdate_databases(kodimonitor)
        kodimonitor.PF.scrobble = lambda plex_id, state: None
        first = SimpleNamespace(
            kodi_id=8950,
            kodi_type='episode',
            plex_id='16390',
            pkc_playback_generation=4,
        )
        second = SimpleNamespace(
            kodi_id=8951,
            kodi_type='episode',
            plex_id='16391',
            pkc_playback_generation=5,
        )
        kodimonitor.app.PLAYSTATE.expected_upnext_handoff = {
            'token': 'verified-token',
            'previous_kodi_id': 8950,
            'previous_kodi_type': 'episode',
            'previous_plex_id': '16390',
            'previous_generation': 4,
            'next_plex_id': '16391',
            'created_at': 100.0,
        }
        kodimonitor.app.PLAYSTATE.started_upnext_handoff = {
            'token': 'verified-token',
            'plex_id': '16391',
            'created_at': 100.0,
        }
        self.assertTrue(kodimonitor._activate_upnext_handoff(
            first, second, now=100.0))

        kodimonitor.app.PLAYSTATE.started_upnext_handoff = None
        self.assertFalse(kodimonitor._activate_upnext_handoff(
            second, first, now=101.0))
        kodimonitor.app.PLAYSTATE.item = None
        kodimonitor._videolibrary_onupdate({
            'item': {'id': 8951, 'type': 'episode'},
            'playcount': 1,
        })

        scrobbles = [
            task for task in backgroundthread.BGThreader.tasks
            if isinstance(task, backgroundthread.FunctionAsTask)
            and task.function is kodimonitor.PF.scrobble
        ]
        self.assertEqual(len(scrobbles), 1)
        self.assertEqual(scrobbles[0].args, ('16390', 'watched'))

    def test_update_after_completed_restore_queues_final_repair(self):
        kodimonitor, _, _, backgroundthread = load_kodimonitor()
        writes = install_onupdate_databases(kodimonitor)
        kodimonitor.PF.scrobble = lambda plex_id, state: None
        kodimonitor.PF.GetPlexMetadata = lambda plex_id: [ET.Element('Video')]
        kodimonitor.API = lambda xml: SimpleNamespace(
            resume_point=lambda: 0.0,
            runtime=lambda: 1434.0,
            viewcount=lambda: 1,
            lastplayed=lambda: '2026-08-04 19:19:41',
        )
        kodimonitor.app.PLAYSTATE.item = None
        kodimonitor._ACTIVE_UPNEXT_HANDOFF = {
            'previous_item': (8950, 'episode'),
            'expires_at': 130.0,
        }
        kodimonitor.monotonic = lambda: 101.0

        update = {
            'item': {'id': 8950, 'type': 'episode'},
            'playcount': 0,
        }
        kodimonitor._videolibrary_onupdate(update)
        first = backgroundthread.BGThreader.tasks[-1]
        first.run()
        kodimonitor._videolibrary_onupdate(update)
        second = backgroundthread.BGThreader.tasks[-1]
        self.assertIsNot(first, second)
        second.run()

        self.assertEqual(len(writes), 4)
        self.assertEqual(kodimonitor._PENDING_PLAYBACK_RESTORES, {})

    def test_update_while_restore_is_in_flight_gets_final_repair(self):
        kodimonitor, _, _, backgroundthread = load_kodimonitor()
        writes = install_onupdate_databases(kodimonitor)
        kodimonitor.PF.scrobble = lambda plex_id, state: None
        kodimonitor.API = lambda xml: SimpleNamespace(
            resume_point=lambda: 0.0,
            runtime=lambda: 1434.0,
            viewcount=lambda: 1,
            lastplayed=lambda: '2026-08-04 19:19:41',
        )
        kodimonitor.app.PLAYSTATE.item = None
        kodimonitor._ACTIVE_UPNEXT_HANDOFF = {
            'previous_item': (8950, 'episode'),
            'expires_at': 130.0,
        }
        kodimonitor.monotonic = lambda: 101.0
        metadata_calls = []

        def metadata(plex_id):
            metadata_calls.append(plex_id)
            if len(metadata_calls) == 1:
                kodimonitor._videolibrary_onupdate({
                    'item': {'id': 8950, 'type': 'episode'},
                    'playcount': 0,
                })
            return [ET.Element('Video')]

        kodimonitor.PF.GetPlexMetadata = metadata
        kodimonitor._videolibrary_onupdate({
            'item': {'id': 8950, 'type': 'episode'},
            'playcount': 0,
        })
        backgroundthread.BGThreader.tasks[-1].run()

        self.assertEqual(len(metadata_calls), 2)
        self.assertEqual(len(writes), 4)
        self.assertEqual(kodimonitor._PENDING_PLAYBACK_RESTORES, {})

    def test_transient_restore_metadata_failure_retries(self):
        kodimonitor, _, _, backgroundthread = load_kodimonitor()
        writes = install_onupdate_databases(kodimonitor)
        kodimonitor.PF.scrobble = lambda plex_id, state: None
        kodimonitor.API = lambda xml: SimpleNamespace(
            resume_point=lambda: 0.0,
            runtime=lambda: 1434.0,
            viewcount=lambda: 1,
            lastplayed=lambda: '2026-08-04 19:19:41',
        )
        kodimonitor.app.PLAYSTATE.item = None
        kodimonitor._ACTIVE_UPNEXT_HANDOFF = {
            'previous_item': (8950, 'episode'),
            'expires_at': 130.0,
        }
        kodimonitor.monotonic = lambda: 101.0
        responses = [None, [ET.Element('Video')]]
        kodimonitor.PF.GetPlexMetadata = lambda plex_id: responses.pop(0)

        kodimonitor._videolibrary_onupdate({
            'item': {'id': 8950, 'type': 'episode'},
            'playcount': 0,
        })
        backgroundthread.BGThreader.tasks[-1].run()

        self.assertEqual(responses, [])
        self.assertEqual(len(writes), 2)
        self.assertEqual(kodimonitor._PENDING_PLAYBACK_RESTORES, {})

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

    def test_completed_episode_write_refreshes_outer_and_nested_containers(self):
        kodimonitor, xbmc, _, _ = load_kodimonitor()
        writes = install_playstate_databases(kodimonitor)
        kodimonitor._playback_progress = lambda status, ended, db_item: (
            0.0, 1434.96, 1, '2026-08-02 08:53:51')

        kodimonitor._record_playstate(playback_status(), ended=True)

        self.assertEqual(writes, [
            (42, 0.0, 1434.96, 1, '2026-08-02 08:53:51'),
        ])
        self.assertEqual(xbmc.commands, ['Container.Refresh'])
        self.assertEqual(kodimonitor.gui_refresh.calls,
                         [(('16388', 'episode'),)])

    def test_partial_episode_threshold_mismatch_uses_scoped_refresh(self):
        kodimonitor, xbmc, _, _ = load_kodimonitor()
        writes = install_playstate_databases(kodimonitor)

        kodimonitor._record_playstate(
            playback_status(position_seconds=1020, total_seconds=1200),
            ended=False)

        self.assertEqual(writes, [
            (42, 1020.0, 1200.0, 0, '2026-08-02 09:00:00'),
        ])
        self.assertEqual(xbmc.commands, ['Container.Refresh'])
        self.assertEqual(kodimonitor.gui_refresh.calls,
                         [(('16388', 'episode'),)])

    def test_movie_write_refreshes_only_outer_container(self):
        kodimonitor, xbmc, _, _ = load_kodimonitor()
        install_playstate_databases(kodimonitor)
        kodimonitor._playback_progress = lambda status, ended, db_item: (
            300.0, 7200.0, 0, '2026-08-02 09:00:00')

        kodimonitor._record_playstate(
            playback_status(plex_type='movie'), ended=False)

        self.assertEqual(xbmc.commands, ['Container.Refresh'])
        self.assertEqual(kodimonitor.gui_refresh.calls, [])


if __name__ == '__main__':
    unittest.main()
