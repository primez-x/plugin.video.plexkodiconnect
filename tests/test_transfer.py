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


def load_transfer(properties, rpc_calls, sleeps):
    xbmc = types.ModuleType('xbmc')
    xbmc.getInfoLabel = lambda label: '21.3'
    xbmc.executeJSONRPC = lambda request: rpc_calls.append(
        json.loads(request)) or '{"result": "OK"}'
    xbmc.sleep = sleeps.append

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
        polls = []

        def sleep(ms):
            polls.append(ms)
            # The service picks up the previous command
            self.properties.pop('plexkodiconnect.command', None)
            self.properties['plexkodiconnect.result.downstream'] = json.dumps(
                {'type': 'other', 'data': True})
        sys.modules['xbmc'].sleep = sleep

        self.transfer.plex_command('PLAY-x')
        self.assertTrue(self.transfer.wait_for_transfer(source='main'))
        self.assertEqual(polls, [self.transfer.POLL_INTERVAL_MS])
        self.assertLessEqual(self.transfer.POLL_INTERVAL_MS, 20)


if __name__ == '__main__':
    unittest.main()
