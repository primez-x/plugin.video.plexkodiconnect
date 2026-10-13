import importlib
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STUBBED_MODULES = (
    'xbmc',
    'resources.lib.playlist_func',
    'resources.lib.plex_api',
    'resources.lib.plex_db',
    'resources.lib.plex_functions',
    'resources.lib.kodi_db',
    'resources.lib.downloadutils',
    'resources.lib.utils',
    'resources.lib.json_rpc',
    'resources.lib.variables',
    'resources.lib.app',
    'resources.lib.exceptions',
    'resources.lib.subtitles',
)


def load_playlist_func(settings, smart_subtitles_enabled, window=None,
                       playing_file='episode2.mkv', wait_callback=None):
    window = {} if window is None else window
    saved = {name: sys.modules.get(name) for name in STUBBED_MODULES}

    def restore():
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    for name in STUBBED_MODULES:
        sys.modules.pop(name, None)

    xbmc = types.ModuleType('xbmc')
    xbmc.getCondVisibility = lambda condition: (
        smart_subtitles_enabled
        and condition == 'System.AddonIsEnabled(service.smartsubtitles)')
    xbmc.Player = lambda: types.SimpleNamespace(getPlayingFile=lambda: playing_file)
    sys.modules['xbmc'] = xbmc

    def stub(name, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    stub('resources.lib.plex_api', API=object)
    stub('resources.lib.plex_db', PlexDB=object)
    stub('resources.lib.plex_functions')
    stub('resources.lib.kodi_db', kodiid_from_filename=lambda *args: None)
    stub('resources.lib.downloadutils', DownloadUtils=object)
    stub('resources.lib.utils',
         settings=lambda setting: settings.get(setting, ''),
         window=lambda prop: window.get(prop, ''),
         cast=lambda func, value: func(value))
    stub('resources.lib.json_rpc')
    stub('resources.lib.variables')
    waits = []

    def wait_for_abort(timeout):
        waits.append(timeout)
        if wait_callback is not None:
            return wait_callback(timeout, window, len(waits))
        return False

    monitor = types.SimpleNamespace(
        waitForAbort=wait_for_abort)
    stub('resources.lib.app', PLAYSTATE=types.SimpleNamespace(item=None),
         APP=types.SimpleNamespace(monitor=monitor), waits=waits)
    stub('resources.lib.exceptions', PlaylistError=Exception)
    stub('resources.lib.subtitles',
         accessible_plex_subtitles=lambda *args, **kwargs: [])
    try:
        return importlib.import_module('resources.lib.playlist_func'), restore
    except Exception:
        restore()
        raise


class InitStreamsSubtitleTests(unittest.TestCase):
    def run_init_streams(self, subtitle_pick, smart_subtitles_enabled,
                         result=None, wait_callback=None):
        window = {} if result is None else {'SmartSubtitles.Result': result}
        module, restore = load_playlist_func(
            {'audioStreamPick': '1', 'subtitleStreamPick': subtitle_pick},
            smart_subtitles_enabled, window, wait_callback=wait_callback)
        self.module = module
        self.addCleanup(restore)
        switched = []
        item = types.SimpleNamespace(
            init_kodi_streams=lambda: None,
            switch_to_plex_stream=switched.append,
            streams_initialized=False)
        module.app.PLAYSTATE.item = item
        module.PlaylistItem.init_streams(item)
        self.assertTrue(item.streams_initialized)
        return switched

    def test_plex_subtitle_choice_is_applied_by_default(self):
        self.assertEqual(self.run_init_streams('0', False),
                         ['video', 'subtitle'])

    def test_smart_subtitles_keeps_its_selection(self):
        self.assertEqual(self.run_init_streams('0', True), ['video'])

    def test_smart_subtitles_selection_for_this_file_is_kept(self):
        for status in ('selected', 'skip'):
            self.assertEqual(
                self.run_init_streams('0', True, status + '|episode2.mkv'), ['video'])

    def test_plex_choice_applies_when_smart_subtitles_chose_nothing(self):
        for status in ('no_match', 'disabled', 'no_streams', 'error'):
            self.assertEqual(
                self.run_init_streams('0', True, status + '|episode2.mkv'),
                ['video', 'subtitle'])

    def test_result_for_another_file_is_waited_for_then_ignored(self):
        self.assertEqual(
            self.run_init_streams('0', True, 'selected|episode1.mkv'),
            ['video', 'subtitle'])
        self.assertEqual(48, len(self.module.app.waits))

    def test_smart_subtitles_selection_after_three_seconds_is_kept(self):
        def publish_late_selection(_timeout, window, wait_count):
            if wait_count == 13:
                window['SmartSubtitles.Result'] = 'selected|episode2.mkv'
            return False

        self.assertEqual(
            ['video'], self.run_init_streams(
                '0', True, 'pending|episode2.mkv', publish_late_selection))
        self.assertEqual(13, len(self.module.app.waits))

    def test_pending_result_falls_back_when_wait_is_aborted(self):
        self.assertEqual(
            ['video', 'subtitle'], self.run_init_streams(
                '0', True, 'pending|episode2.mkv',
                lambda _timeout, _window, _wait_count: True))
        self.assertEqual(1, len(self.module.app.waits))

    def test_kodi_pick_never_switches_subtitles(self):
        self.assertEqual(self.run_init_streams('1', False), ['video'])


if __name__ == '__main__':
    unittest.main()
