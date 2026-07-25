import importlib
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_default_entrypoint():
    resources_lib = sys.modules.get('resources.lib')
    if resources_lib is not None and hasattr(resources_lib, 'watchlist'):
        delattr(resources_lib, 'watchlist')
    for module_name in (
        'xbmc',
        'xbmcgui',
        'xbmcplugin',
        'resources.lib.entrypoint',
        'resources.lib.loghandler',
        'resources.lib.transfer',
        'resources.lib.watchlist',
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

    watchlist = types.ModuleType('resources.lib.watchlist')
    watchlist.set_tmdb = lambda params, desired: commands.append(
        ('watchlist_tmdb', dict(params), desired)
    )
    watchlist.set_key = lambda params, desired: commands.append(
        ('watchlist_key', dict(params), desired)
    )
    watchlist.status_tmdb = commands.append
    watchlist.status_key = commands.append
    watchlist.status_monitor = lambda: commands.append('watchlist_monitor')
    watchlist.bootstrap_status_tmdb = lambda params: None
    watchlist.bootstrap_status_key = lambda params: None
    sys.modules['resources.lib.watchlist'] = watchlist

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

    def test_tmdb_watchlist_mode_uses_the_direct_verified_worker(self):
        default, commands = load_default_entrypoint()

        default.triage(
            'watchlist_add_tmdb',
            {'tmdb_id': '603', 'tmdb_type': 'movie'},
            '',
            '',
            '',
        )

        self.assertEqual(
            commands,
            [('watchlist_tmdb', {'tmdb_id': '603', 'tmdb_type': 'movie'}, 'present')],
        )

    def test_watchlist_status_modes_enqueue_the_service_worker(self):
        default, commands = load_default_entrypoint()

        default.triage(
            'watchlist_status_tmdb',
            {'tmdb_id': '603', 'tmdb_type': 'movie'},
            '',
            '',
            '',
        )
        default.triage('watchlist_status_monitor', {}, '', '', '')

        self.assertEqual(
            commands,
            [
                'WATCHLIST_STATUS_TMDB?tmdb_id=603&tmdb_type=movie',
                'WATCHLIST_STATUS_MONITOR',
            ],
        )


if __name__ == '__main__':
    unittest.main()
