import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_playback_starter():
    lib_package = sys.modules.get('resources.lib')
    if lib_package is not None:
        for attribute in (
                'app', 'backgroundthread', 'contextmenu', 'playback',
                'playback_starter', 'transfer', 'utils'):
            lib_package.__dict__.pop(attribute, None)
    for module_name in (
            'resources.lib.playback_starter',
            'resources.lib.backgroundthread',
            'resources.lib.playback',
            'resources.lib.transfer',
            'resources.lib.app',
            'resources.lib.utils',
            'resources.lib.contextmenu'):
        sys.modules.pop(module_name, None)

    backgroundthread = types.ModuleType('resources.lib.backgroundthread')

    class Task(object):
        def __init__(self, priority=None):
            self.priority = priority

    backgroundthread.Task = Task

    calls = []
    playback = types.ModuleType('resources.lib.playback')
    playback.playback_triage = lambda **kwargs: calls.append(kwargs)

    transfer = types.ModuleType('resources.lib.transfer')
    transfer.send = lambda *args, **kwargs: None
    transfer.wait_for_transfer = lambda *args, **kwargs: None

    app = types.ModuleType('resources.lib.app')
    app.PLAYSTATE = SimpleNamespace(
        force_transcode=False,
        context_menu_play=False,
    )

    class DummyLock(object):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    app.APP = SimpleNamespace(lock_playqueues=DummyLock())

    utils = types.ModuleType('resources.lib.utils')
    utils.parse_qsl = parse_qsl

    contextmenu = types.ModuleType('resources.lib.contextmenu')
    contextmenu.menu = SimpleNamespace(ContextMenu=lambda **kwargs: None)

    for module_name, module in {
            'resources.lib.backgroundthread': backgroundthread,
            'resources.lib.playback': playback,
            'resources.lib.transfer': transfer,
            'resources.lib.app': app,
            'resources.lib.utils': utils,
            'resources.lib.contextmenu': contextmenu,
    }.items():
        sys.modules[module_name] = module

    module = importlib.import_module('resources.lib.playback_starter')
    return module, app, calls


class PlaybackStarterTests(unittest.TestCase):
    def test_upnext_invocation_marks_exact_handoff_before_playback(self):
        playback_starter, app, calls = load_playback_starter()
        task = playback_starter.PlaybackTask(
            'plugin://plugin.video.plexkodiconnect?'
            'mode=play&plex_id=16391&plex_type=episode&handle=-1&'
            'pkc_upnext=verified-token')

        task.run()

        self.assertEqual(
            {key: app.PLAYSTATE.started_upnext_handoff[key]
             for key in ('token', 'plex_id')},
            {'token': 'verified-token', 'plex_id': '16391'},
        )
        self.assertIsInstance(
            app.PLAYSTATE.started_upnext_handoff['created_at'], float)
        self.assertEqual(calls[0]['plex_id'], '16391')

    def test_regular_playback_clears_stale_started_handoff(self):
        playback_starter, app, _ = load_playback_starter()
        app.PLAYSTATE.started_upnext_handoff = {
            'token': 'stale-token',
            'plex_id': '1',
        }
        task = playback_starter.PlaybackTask(
            'plugin://plugin.video.plexkodiconnect?'
            'mode=play&plex_id=7&plex_type=episode&handle=-1')

        task.run()

        self.assertIsNone(app.PLAYSTATE.started_upnext_handoff)


if __name__ == '__main__':
    unittest.main()
