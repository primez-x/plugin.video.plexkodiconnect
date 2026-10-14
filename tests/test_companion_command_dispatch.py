import importlib
import json
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as etree


REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = REPO_ROOT / 'resources' / 'lib'
MODULE_PREFIX = '_companion_command_contract'


class KodiRPCRecorder(object):
    def __init__(self):
        self.calls = []
        self.error = None

    def execute(self, query):
        request = json.loads(query)
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if request['method'] == 'Player.GetActivePlayers':
            response = {'result': [{'playerid': 1, 'type': 'video'}]}
        elif request['method'] == 'Player.GetProperties':
            response = {'result': {'position': 5}}
        else:
            response = {'result': False}
        return json.dumps(response)


class QueueRecorder(object):
    def __init__(self):
        self.events = []

    def put(self, event):
        self.events.append(event)


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    sys.modules[name] = result
    return result


class CompanionCommandContractTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)

        self.rpc = KodiRPCRecorder()
        kodi = module('xbmc', executeJSONRPC=self.rpc.execute,
                      Player=lambda: types.SimpleNamespace(play=None))
        self.kodi = kodi

        root = types.ModuleType(MODULE_PREFIX)
        root.__path__ = [str(LIB_ROOT)]
        sys.modules[MODULE_PREFIX] = root

        self.variables = module(
            MODULE_PREFIX + '.variables',
            ALEXA_TO_COMPANION={
                'queryKey': 'key', 'queryOffset': 'offset',
                'queryMachineIdentifier': 'machineIdentifier',
                'queryProtocol': 'protocol', 'queryAddress': 'address',
                'queryPort': 'port', 'queryContainerKey': 'containerKey',
                'queryToken': 'token'
            },
            KODI_TYPE_MOVIE='movie', KODI_TYPE_SHOW='show',
            KODI_TYPE_SEASON='season', KODI_TYPE_EPISODE='episode',
            KODI_TYPE_ARTIST='artist', KODI_TYPE_ALBUM='album',
            KODI_TYPE_SONG='song', KODI_TYPE_SET='set')
        module(MODULE_PREFIX + '.kodi_constants',
               FIELDS_MOVIES=[], FIELDS_TVSHOWS=[], FIELDS_SEASON=[],
               FIELDS_EPISODES=[], FIELDS_ARTISTS=[], FIELDS_ALBUMS=[],
               FIELDS_SONGS=[])
        self.queue = QueueRecorder()
        self.app = module(
            MODULE_PREFIX + '.app',
            APP=types.SimpleNamespace(
                companion_queue=self.queue,
                lock_playqueues=threading.RLock(),
                player=types.SimpleNamespace(play=None)),
            PLAYQUEUES={})
        module(MODULE_PREFIX + '.plex_functions',
               GetPlexKeyNumber=lambda key: (None, None),
               ParseContainerKey=lambda key: (None, None, {}),
               GetPlexMetadata=lambda key: None)
        module(MODULE_PREFIX + '.utils',
               cast=lambda kind, value: None if value is None else kind(value),
               extend_url=lambda *args, **kwargs: '')
        module(MODULE_PREFIX + '.playlist_func')
        module(MODULE_PREFIX + '.playback')
        module(MODULE_PREFIX + '.plex_api', API=object)
        module(MODULE_PREFIX + '.exceptions', PlaylistError=Exception)

        companion_package = types.ModuleType(MODULE_PREFIX + '.plex_companion')
        companion_package.__path__ = [str(LIB_ROOT / 'plex_companion')]
        sys.modules[MODULE_PREFIX + '.plex_companion'] = companion_package

        self.js = importlib.import_module(MODULE_PREFIX + '.json_rpc')
        self.legacy = importlib.import_module(MODULE_PREFIX + '.companion')
        self.native = importlib.import_module(
            MODULE_PREFIX + '.plex_companion.processing')

    def dispatch(self, gateway, path, params=None, command=None):
        if gateway == 'legacy':
            return self.legacy.process_command(path, params or {})
        return self.native.process_command(cmd=command, path=path,
                                           params=params)

    def last_rpc(self):
        return self.rpc.calls[-1]

    def test_all_shared_routes_keep_their_kodi_rpc_contract(self):
        routes = (
            ('player/playback/play', 'Player.PlayPause',
             {'playerid': 1, 'play': True}),
            ('player/playback/pause', 'Player.PlayPause',
             {'playerid': 1, 'play': False}),
            ('player/playback/stop', 'Player.Stop', {'playerid': 1}),
            ('player/playback/seekTo', 'Player.Seek',
             {'playerid': 1, 'value': {'time': {
                 'hours': 0, 'minutes': 0, 'seconds': 12,
                 'milliseconds': 500}}}),
            ('player/playback/stepForward', 'Player.Seek',
             {'playerid': 1, 'value': 'smallforward'}),
            ('player/playback/stepBack', 'Player.Seek',
             {'playerid': 1, 'value': 'smallbackward'}),
            ('player/playback/skipNext', 'Player.GoTo',
             {'playerid': 1, 'to': 'next'}),
            ('player/playback/skipPrevious', 'Player.GoTo',
             {'playerid': 1, 'to': 4}),
            ('player/navigation/moveUp', 'Input.Up', None),
            ('player/navigation/moveDown', 'Input.Down', None),
            ('player/navigation/moveLeft', 'Input.Left', None),
            ('player/navigation/moveRight', 'Input.Right', None),
            ('player/navigation/select', 'Input.Select', None),
            ('player/navigation/home', 'Input.Home', None),
            ('player/navigation/back', 'Input.Back', None),
        )
        for gateway in ('legacy', 'native'):
            for route, method, params in routes:
                with self.subTest(gateway=gateway, route=route):
                    self.rpc.calls.clear()
                    path = route if gateway == 'legacy' else '/' + route
                    route_params = {'offset': '12500'}
                    self.assertTrue(self.dispatch(gateway, path, route_params))
                    self.assertEqual(self.last_rpc()['method'], method)
                    self.assertEqual(self.last_rpc().get('params'), params)

    def test_shared_routes_return_true_when_kodi_returns_false(self):
        for gateway, path in (
                ('legacy', 'player/navigation/home'),
                ('native', '/player/navigation/home')):
            with self.subTest(gateway=gateway):
                self.rpc.calls.clear()
                self.assertTrue(self.dispatch(gateway, path, {}))
                self.assertEqual(self.last_rpc()['method'], 'Input.Home')

    def test_shared_dispatch_resolves_kodi_methods_at_call_time(self):
        for gateway, path in (
                ('legacy', 'player/navigation/home'),
                ('native', '/player/navigation/home')):
            with self.subTest(gateway=gateway):
                with patch.object(self.js, 'input_home') as input_home:
                    self.assertTrue(self.dispatch(gateway, path, {}))
                input_home.assert_called_once_with()

    def test_seek_defaults_to_zero_for_both_gateways(self):
        for gateway, path in (
                ('legacy', 'player/playback/seekTo'),
                ('native', '/player/playback/seekTo')):
            with self.subTest(gateway=gateway):
                self.rpc.calls.clear()
                self.assertTrue(self.dispatch(gateway, path, {}))
                self.assertEqual(self.last_rpc()['params']['value']['time'], {
                    'hours': 0, 'minutes': 0, 'seconds': 0,
                    'milliseconds': 0})

    def test_seek_rejects_malformed_offsets_for_both_gateways(self):
        for gateway, path in (
                ('legacy', 'player/playback/seekTo'),
                ('native', '/player/playback/seekTo')):
            with self.subTest(gateway=gateway):
                with self.assertRaises(ValueError):
                    self.dispatch(gateway, path, {'offset': 'bad'})

    def test_legacy_converts_alexa_fields_before_common_dispatch(self):
        params = {'deviceName': 'Alexa', 'queryOffset': '2500'}
        self.assertTrue(self.legacy.process_command(
            'player/playback/seekTo', params))
        self.assertNotIn('queryOffset', params)
        self.assertEqual(params['offset'], '2500')
        self.assertEqual(self.last_rpc()['params']['value']['time'], {
            'hours': 0, 'minutes': 0, 'seconds': 2,
            'milliseconds': 500})

    def test_legacy_play_media_converts_alexa_fields_before_queueing(self):
        params = {'deviceName': 'Alexa', 'queryKey': '/library/metadata/7'}
        self.assertTrue(self.legacy.process_command(
            'player/playback/playMedia', params))
        self.assertEqual(self.queue.events, [{
            'action': 'alexa',
            'data': {'deviceName': 'Alexa', 'key': '/library/metadata/7'}
        }])
        self.assertTrue(self.legacy.process_command(
            'player/playback/playMedia', {'deviceName': 'Plex Web'}))
        self.assertEqual(self.queue.events[-1], {
            'action': 'playlist', 'data': {'deviceName': 'Plex Web'}})

    def test_legacy_gateway_routes_keep_queue_and_skip_actions(self):
        self.assertTrue(self.legacy.process_command(
            'player/playback/refreshPlayQueue', {'playQueueID': '44'}))
        self.assertEqual(self.queue.events[-1], {
            'action': 'refreshPlayQueue', 'data': {'playQueueID': '44'}})

        skip_params = {'playQueueItemID': '9', 'key': '/library/metadata/7'}
        with patch.object(self.legacy, 'skip_to') as skip_to:
            self.assertTrue(self.legacy.process_command(
                'player/playback/skipTo', skip_params))
        skip_to.assert_called_once_with(skip_params)

        stream_params = {'type': 'video', 'audioStreamID': '2'}
        self.assertTrue(self.legacy.process_command(
            'player/playback/setStreams', stream_params))
        self.assertEqual(self.queue.events[-1], {
            'action': 'setStreams', 'data': stream_params})

    def test_native_play_media_routes_keep_node_and_playlist_actions(self):
        params = {'containerKey': '/playQueues/44', 'type': 'video',
                  'key': '/library/metadata/7', 'offset': '2500',
                  'token': 'safe-token'}
        with patch.object(self.native, 'process_playlist') as process_playlist:
            self.assertTrue(self.native.process_command(
                path='/player/playback/playMedia', params=params))
        process_playlist.assert_called_once_with(
            '/playQueues/44', 'video', '/library/metadata/7', '2500',
            'safe-token')

        command = etree.Element('Command', attrib={
            'path': '/player/playback/playMedia',
            'queryAddress': 'node.plexapp.com',
            'queryKey': '/library/metadata/8', 'queryToken': 'node-token',
            'queryOffset': '3000'})
        with patch.object(self.native, 'process_node') as process_node:
            self.assertTrue(self.native.process_command(command))
        process_node.assert_called_once_with(
            '/library/metadata/8', 'node-token', '3000')

    def test_native_gateway_routes_keep_refresh_skip_stream_and_timeline_actions(self):
        with patch.object(self.native, 'process_refresh') as process_refresh:
            self.assertTrue(self.native.process_command(
                path='/player/playback/refreshPlayQueue',
                params={'playQueueID': '44'}))
        process_refresh.assert_called_once_with('44')

        with patch.object(self.native, 'skip_to') as skip_to:
            self.assertTrue(self.native.process_command(
                path='/player/playback/skipTo',
                params={'playQueueItemID': '9', 'key': '/library/metadata/7'}))
        skip_to.assert_called_once_with('9', '/library/metadata/7')

        with patch.object(self.native, 'process_streams') as process_streams:
            self.assertTrue(self.native.process_command(
                path='/player/playback/setStreams',
                params={'type': 'video', 'videoStreamID': '1',
                        'audioStreamID': '2', 'subtitleStreamID': '3'}))
        process_streams.assert_called_once_with('video', 1, 2, 3)

        self.assertTrue(self.native.process_command(
            path='/player/timeline/subscribe', params={}))
        self.assertTrue(self.native.process_command(
            path='/player/timeline/unsubscribe', params={}))

    def test_volume_missing_value_keeps_gateway_specific_behavior(self):
        with self.assertLogs('PLEX.companion', level='ERROR'):
            self.assertFalse(self.legacy.process_command(
                'player/playback/setParameters', {}))
        with self.assertRaises(TypeError):
            self.native.process_command(
                path='/player/playback/setParameters', params={})

    def test_volume_with_value_uses_the_same_kodi_rpc(self):
        self.assertTrue(self.legacy.process_command(
            'player/playback/setParameters', {'volume': '35'}))
        self.assertEqual(self.last_rpc()['method'], 'Application.SetVolume')
        self.assertEqual(self.last_rpc()['params'], {'volume': 35})

        self.rpc.calls.clear()
        self.assertTrue(self.native.process_command(
            path='/player/playback/setParameters', params={'volume': '35'}))
        self.assertEqual(self.last_rpc()['method'], 'Application.SetVolume')
        self.assertEqual(self.last_rpc()['params'], {'volume': 35})

    def test_path_acceptance_remains_gateway_specific(self):
        with self.assertLogs('PLEX.companion', level='ERROR'):
            self.assertFalse(self.legacy.process_command(
                '/player/navigation/home', {}))
        self.assertTrue(self.native.process_command(
            path='player/navigation/home', params={}))

    def test_unknown_routes_fail_without_logging_parameter_tokens(self):
        secret = 'parameter-token-value'
        with self.assertLogs('PLEX.companion', level='ERROR') as legacy_logs:
            self.assertFalse(self.legacy.process_command(
                'player/navigation/unknown', {'token': secret}))
        with self.assertLogs('PLEX.companion.processing', level='ERROR') as native_logs:
            self.assertFalse(self.native.process_command(
                path='/player/navigation/unknown', params={'token': secret}))
        self.assertNotIn(secret, '\n'.join(legacy_logs.output))
        self.assertNotIn(secret, '\n'.join(native_logs.output))

    def test_kodi_rpc_errors_propagate_from_both_gateways(self):
        self.rpc.error = RuntimeError('transport failure')
        for gateway, path in (
                ('legacy', 'player/navigation/home'),
                ('native', '/player/navigation/home')):
            with self.subTest(gateway=gateway):
                with self.assertRaisesRegex(RuntimeError, 'transport failure'):
                    self.dispatch(gateway, path, {})


if __name__ == '__main__':
    unittest.main()
