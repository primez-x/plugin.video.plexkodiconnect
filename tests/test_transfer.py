import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _Recorder(object):
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name,) + args)
        return record


class _FakeListItem(object):
    instances = []

    def __init__(self, label=None, label2=None, path=None, offscreen=False):
        self.video_tag = _Recorder()
        self.music_tag = _Recorder()
        self.calls = []
        _FakeListItem.instances.append(self)

    def getVideoInfoTag(self):
        return self.video_tag

    def getMusicInfoTag(self):
        return self.music_tag

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


class _FakeMonitor(object):
    abort = False
    on_wait = None

    def waitForAbort(self, timeout=0):
        if _FakeMonitor.on_wait:
            _FakeMonitor.on_wait(timeout)
        return _FakeMonitor.abort

    def abortRequested(self):
        return _FakeMonitor.abort


def load_transfer(properties, rpc_calls, sleeps):
    _FakeListItem.instances = []
    _FakeMonitor.abort = False
    _FakeMonitor.on_wait = lambda timeout: sleeps.append(
        int(round(timeout * 1000)))
    xbmc = types.ModuleType('xbmc')
    xbmc.getInfoLabel = lambda label: '21.3'
    xbmc.executeJSONRPC = lambda request: rpc_calls.append(
        json.loads(request)) or '{"result": "OK"}'
    xbmc.sleep = sleeps.append
    xbmc.Monitor = _FakeMonitor
    xbmc.Actor = lambda name, role='': ('Actor', name, role)
    xbmc.VideoStreamDetail = _Recorder
    xbmc.AudioStreamDetail = _Recorder
    xbmc.SubtitleStreamDetail = _Recorder

    class Window(object):
        def __init__(self, window_id):
            pass

        def getProperty(self, key):
            return properties.get(key, '')

        def setProperty(self, key, value):
            properties[key] = value

        def clearProperty(self, key):
            properties.pop(key, None)

    xbmcgui = types.ModuleType('xbmcgui')
    xbmcgui.Window = Window
    xbmcgui.ListItem = _FakeListItem
    sys.modules['xbmc'] = xbmc
    sys.modules['xbmcgui'] = xbmcgui
    sys.modules.pop('resources.lib.transfer', None)
    package = importlib.import_module('resources.lib')
    package.__dict__.pop('transfer', None)
    return importlib.import_module('resources.lib.transfer')


class TransferTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        self.properties = {}
        self.rpc_calls = []
        self.sleeps = []
        self.transfer = load_transfer(
            self.properties, self.rpc_calls, self.sleeps)

    def test_plex_command_wakes_the_service_after_posting_the_command(self):
        self.transfer.plex_command('skip-marker')

        self.assertEqual(self.properties['plexkodiconnect.command'],
                         'skip-marker')
        self.assertEqual(len(self.rpc_calls), 1)
        request = self.rpc_calls[0]
        self.assertEqual(request['method'], 'JSONRPC.NotifyAll')
        self.assertEqual(request['params']['sender'],
                         'plugin.video.plexkodiconnect.SIGNAL')
        self.assertEqual(request['params']['message'], 'pkc_command')

    def test_a_failing_notification_never_loses_the_command(self):
        def broken(request):
            raise RuntimeError('Kodi is shutting down')
        sys.modules['xbmc'].executeJSONRPC = broken

        self.transfer.plex_command('full-scan')

        self.assertEqual(self.properties['plexkodiconnect.command'],
                         'full-scan')

    def test_hand_offs_poll_at_the_short_interval(self):
        self.properties['plexkodiconnect.command'] = 'previous'

        def on_wait(timeout):
            self.sleeps.append(int(round(timeout * 1000)))
            # The service picks up the previous command
            self.properties.pop('plexkodiconnect.command', None)
            self.properties['plexkodiconnect.result.downstream'] = json.dumps(
                {'type': 'other', 'data': True})
        _FakeMonitor.on_wait = on_wait

        self.transfer.plex_command('PLAY-x')
        self.assertTrue(self.transfer.wait_for_transfer(source='main'))
        self.assertEqual(self.sleeps, [self.transfer.POLL_INTERVAL_MS])
        self.assertLessEqual(self.transfer.POLL_INTERVAL_MS, 20)

    def test_plex_command_drops_instead_of_hanging_on_a_stuck_command(self):
        self.properties['plexkodiconnect.command'] = 'stuck'
        with patch.object(self.transfer, 'COMMAND_TIMEOUT_SECONDS', 0.05):
            self.assertFalse(self.transfer.plex_command('PLAY-x'))
        # The stuck command is left untouched, ours was not posted over it
        self.assertEqual(self.properties['plexkodiconnect.command'], 'stuck')

    def test_wait_for_transfer_times_out_with_none(self):
        self.assertIsNone(
            self.transfer.wait_for_transfer(source='main', timeout=0.05))

    def test_waits_stop_when_kodi_aborts(self):
        self.properties['plexkodiconnect.command'] = 'stuck'
        _FakeMonitor.abort = True
        self.assertFalse(self.transfer.plex_command('PLAY-x'))
        self.assertIsNone(
            self.transfer.wait_for_transfer(source='main', timeout=60))

    def test_convert_pkc_to_listitem_uses_info_tags(self):
        item = self.transfer.PKCListItem(label='Alien', path='plugin://x')
        item.setInfo('Video', {
            'title': 'Alien', 'year': 1979, 'rating': 8.5,
            'genre': 'Sci-Fi / Horror', 'mediatype': 'movie',
            'castandrole': [('Sigourney Weaver', 'Ripley')]})
        item.addStreamInfo('video', {'codec': 'hevc', 'width': 3840,
                                     'height': 2160, 'aspect': 1.78})
        item.addStreamInfo('audio', {'codec': 'truehd', 'channels': 6,
                                     'language': 'eng'})

        result = self.transfer.convert_pkc_to_listitem(item)

        self.assertIs(result, _FakeListItem.instances[-1])
        calls = result.video_tag.calls
        self.assertIn(('setTitle', 'Alien'), calls)
        self.assertIn(('setYear', 1979), calls)
        self.assertIn(('setGenres', ['Sci-Fi', 'Horror']), calls)
        self.assertIn(('setMediaType', 'movie'), calls)
        self.assertFalse(
            any(call[0] in ('setInfo', 'addStreamInfo')
                for call in result.calls))


if __name__ == '__main__':
    unittest.main()
