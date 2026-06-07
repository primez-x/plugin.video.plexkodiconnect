import importlib
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_service_entry():
    module_names = (
        'xbmc',
        'xbmcvfs',
        'resources.lib.service_entry',
        'resources.lib.utils',
        'resources.lib.clientinfo',
        'resources.lib.initialsetup',
        'resources.lib.kodimonitor',
        'resources.lib.sync',
        'resources.lib.library_sync',
        'resources.lib.websocket_client',
        'resources.lib.kodi_db',
        'resources.lib.plex_db',
        'resources.lib.plex_companion',
        'resources.lib.plex_functions',
        'resources.lib.playback_starter',
        'resources.lib.variables',
        'resources.lib.app',
        'resources.lib.loghandler',
        'resources.lib.backgroundthread',
        'resources.lib.skip_plex_markers',
        'resources.lib.downloadutils',
        'resources.lib.windows',
        'resources.lib.windows.userselect',
    )
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    sys.modules['xbmc'] = types.ModuleType('xbmc')
    sys.modules['xbmcvfs'] = types.ModuleType('xbmcvfs')

    modules = {}
    for module_name in module_names[3:]:
        modules[module_name] = types.ModuleType(module_name)
        sys.modules[module_name] = modules[module_name]

    modules['resources.lib.loghandler'].config = lambda: None

    userselect = modules['resources.lib.windows.userselect']
    modules['resources.lib.windows'].userselect = userselect

    return importlib.import_module('resources.lib.service_entry')


class ServiceEntryStartupTests(unittest.TestCase):
    def test_service_entry_imports_kodi_db_for_startup_normalization(self):
        service_entry = load_service_entry()

        self.assertIs(service_entry.kodi_db, sys.modules['resources.lib.kodi_db'])

    def test_visible_skip_marker_countdown_uses_fast_service_polling(self):
        service_entry = load_service_entry()

        self.assertEqual(
            service_entry.service_loop_sleep_ms(skip_marker_countdown_visible=True),
            33)
        self.assertEqual(
            service_entry.service_loop_sleep_ms(skip_marker_countdown_visible=False),
            200)


if __name__ == '__main__':
    unittest.main()
