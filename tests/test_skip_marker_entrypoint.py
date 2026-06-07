import importlib
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_default_entrypoint():
    for module_name in (
        'xbmc',
        'xbmcgui',
        'xbmcplugin',
        'resources.lib.entrypoint',
        'resources.lib.loghandler',
        'resources.lib.transfer',
        'resources.lib.utils',
        'resources.lib.variables',
        'default',
    ):
        sys.modules.pop(module_name, None)

    sys.modules['xbmc'] = types.ModuleType('xbmc')
    sys.modules['xbmcgui'] = types.ModuleType('xbmcgui')
    xbmcplugin = types.ModuleType('xbmcplugin')
    xbmcplugin.endOfDirectory = lambda *args, **kwargs: None
    sys.modules['xbmcplugin'] = xbmcplugin

    entrypoint = types.ModuleType('resources.lib.entrypoint')
    entrypoint.ListingException = Exception
    entrypoint.show_main_menu = lambda *args, **kwargs: None
    sys.modules['resources.lib.entrypoint'] = entrypoint

    loghandler = types.ModuleType('resources.lib.loghandler')
    loghandler.config = lambda: None
    sys.modules['resources.lib.loghandler'] = loghandler

    commands = []
    transfer = types.ModuleType('resources.lib.transfer')
    transfer.plex_command = commands.append
    sys.modules['resources.lib.transfer'] = transfer

    utils = types.ModuleType('resources.lib.utils')
    utils.lang = lambda string_id: str(string_id)
    sys.modules['resources.lib.utils'] = utils

    variables = types.ModuleType('resources.lib.variables')
    variables.ADDON_ID = 'plugin.video.plexkodiconnect'
    sys.modules['resources.lib.variables'] = variables

    module = importlib.import_module('default')
    module.argv = ['plugin://plugin.video.plexkodiconnect', '-1', '']
    return module, commands


class SkipMarkerEntrypointTests(unittest.TestCase):
    def test_skip_marker_mode_sends_service_command(self):
        default, commands = load_default_entrypoint()

        default.triage('skip_marker', {}, '', '', '')

        self.assertEqual(commands, ['skip-marker'])


if __name__ == '__main__':
    unittest.main()
