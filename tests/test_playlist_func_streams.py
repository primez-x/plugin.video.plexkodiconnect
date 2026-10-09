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


def load_playlist_func(settings, smart_subtitles_enabled):
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
         cast=lambda func, value: func(value))
    stub('resources.lib.json_rpc')
    stub('resources.lib.variables')
    stub('resources.lib.app', PLAYSTATE=types.SimpleNamespace(item=None))
    stub('resources.lib.exceptions', PlaylistError=Exception)
    stub('resources.lib.subtitles',
         accessible_plex_subtitles=lambda *args, **kwargs: [])
    try:
        return importlib.import_module('resources.lib.playlist_func'), restore
    except Exception:
        restore()
        raise


class InitStreamsSubtitleTests(unittest.TestCase):
    def run_init_streams(self, subtitle_pick, smart_subtitles_enabled):
        module, restore = load_playlist_func(
            {'audioStreamPick': '1', 'subtitleStreamPick': subtitle_pick},
            smart_subtitles_enabled)
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

    def test_kodi_pick_never_switches_subtitles(self):
        self.assertEqual(self.run_init_streams('1', False), ['video'])


if __name__ == '__main__':
    unittest.main()
