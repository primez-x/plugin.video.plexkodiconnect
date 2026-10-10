import importlib.util
import sys
import threading
import types
import unittest
import xml.etree.ElementTree as etree
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPANION_DIR = REPO_ROOT / 'resources' / 'lib' / 'plex_companion'
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _stopped_timeline():
    xml = etree.Element('MediaContainer')
    for typus in ('music', 'video', 'photo'):
        etree.SubElement(xml, 'Timeline', attrib={'type': typus,
                                                  'state': 'stopped'})
    return xml


def load_playstate(sent):
    def stub(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        sys.modules[name] = module
        return module

    class KillableThread(object):
        def __init__(self, *args, **kwargs):
            pass

    def communicate(method, url, **kwargs):
        sent.append(dict(kwargs['params']))
        return SimpleNamespace(ok=True)

    package = stub('resources.lib.plex_companion')
    package.__path__ = [str(COMPANION_DIR)]
    stub('resources.lib.plex_companion.common',
         communicate=communicate, log_error=None, UUIDStr=str,
         Subscriber=object, timeline=None,
         stopped_timeline=_stopped_timeline,
         create_requests_session=lambda: SimpleNamespace(get=None),
         proxy_params=lambda: {'X-Plex-Client-Identifier': 'pkc'})
    stub('resources.lib.plex_companion.playqueue', compare_playqueues=None)
    stub('resources.lib.plex_companion.webserver',
         ThreadedHTTPServer=None, CompanionHandlerClassFactory=None)
    stub('resources.lib.plex_companion.plexgdm', plexgdm=lambda: None)
    for name in ('json_rpc', 'variables', 'timing'):
        stub('resources.lib.%s' % name)
    stub('resources.lib.backgroundthread', KillableThread=KillableThread)
    stub('resources.lib.app',
         CONN=SimpleNamespace(server='http://pms:32400'),
         APP=SimpleNamespace(lock_subscriber=threading.RLock()))
    stub('resources.lib.utils', cached_setting=lambda key: 'true')
    package_lib = sys.modules['resources.lib']
    for name in ('json_rpc', 'variables', 'timing', 'backgroundthread',
                 'app', 'utils', 'plex_companion'):
        package_lib.__dict__.pop(name, None)

    spec = importlib.util.spec_from_file_location(
        'resources.lib.plex_companion.playstate', COMPANION_DIR / 'playstate.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['resources.lib.plex_companion.playstate'] = module
    spec.loader.exec_module(module)
    return module


def message(state, time_ms, rating_key='100'):
    xml = etree.Element('MediaContainer')
    etree.SubElement(xml, 'Timeline', attrib={'type': 'music',
                                              'state': 'stopped'})
    etree.SubElement(xml, 'Timeline', attrib={
        'type': 'video', 'state': state, 'time': str(time_ms),
        'ratingKey': rating_key, 'key': '/library/metadata/%s' % rating_key})
    etree.SubElement(xml, 'Timeline', attrib={'type': 'photo',
                                              'state': 'stopped'})
    return xml


class CompanionPlaystateTimelineTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        import resources.lib as package
        saved = dict(vars(package))

        def restore():
            for name in list(vars(package)):
                if name not in saved:
                    delattr(package, name)
            for name, value in saved.items():
                setattr(package, name, value)
        self.addCleanup(restore)
        self.sent = []
        self.playstate = load_playstate(self.sent)
        self.clock = [1000.0]
        self.playstate.monotonic = lambda: self.clock[0]
        self.mgr = self.playstate.PlaystateMgr(companion_enabled=False)

    def _tick(self, state, time_ms, rating_key='100'):
        self.mgr.pms_timeline({1: {'playerid': 1}},
                              message(state, time_ms, rating_key))
        self.clock[0] += 1

    def _sent(self, key='time'):
        return [(params['state'], params.get(key)) for params in self.sent]

    def test_steady_playback_reports_every_ten_seconds_without_subscribers(self):
        for second in range(12):
            self._tick('playing', 60000 + second * 1000)

        self.assertEqual(self._sent(), [('playing', '60000'),
                                        ('playing', '70000')])

    def test_stop_reports_the_latest_position_not_the_last_sent_one(self):
        for second in range(5):
            self._tick('playing', 60000 + second * 1000)
        self.mgr.send_stop()

        # Only 60000 was sent while playing; the stop must not rewind the
        # PMS resume point to it
        video_stops = [params for params in self.sent
                       if params['state'] == 'stopped' and 'ratingKey' in params]
        self.assertEqual(len(video_stops), 1)
        self.assertEqual(video_stops[0]['time'], '64000')
        self.assertEqual(video_stops[0]['ratingKey'], '100')

    def test_pause_resume_and_seek_are_reported_immediately(self):
        self._tick('playing', 60000)
        self._tick('paused', 61000)
        self._tick('paused', 61000)
        self._tick('playing', 61000)
        self._tick('playing', 900000)

        self.assertEqual(self._sent(), [('playing', '60000'),
                                        ('paused', '61000'),
                                        ('playing', '61000'),
                                        ('playing', '900000')])

    def test_companion_subscribers_get_the_one_second_cadence(self):
        self.mgr.subscribers = {'controller': object()}
        for second in range(3):
            self._tick('playing', 60000 + second * 1000)

        self.assertEqual(len(self.sent), 3)


if __name__ == '__main__':
    unittest.main()
