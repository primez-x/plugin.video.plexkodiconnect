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

    def test_failed_discover_cache_write_requeues_the_background_refresh(self):
        service_entry = load_service_entry()
        service = object.__new__(service_entry.Service)
        completed = []
        service_entry.app.ACCOUNT = types.SimpleNamespace(authenticated=True)
        service_entry.discover_cache = types.SimpleNamespace(
            account_matches=lambda account_hash: account_hash == "account-a",
            refresh_hosts=lambda cache_kind: ("discover.provider.plex.tv",),
            read_xml=lambda *args: ("stale", [object()]),
            CACHE_FRESH="fresh",
            put_xml=lambda *args, **kwargs: False,
            finish_refresh=lambda request, success: completed.append(success) or True,
        )
        entrypoint = types.ModuleType('resources.lib.entrypoint')
        entrypoint._download_provider_xmls = lambda *args: [object()]
        resources_lib = sys.modules['resources.lib']
        previous_module = sys.modules.get('resources.lib.entrypoint')
        previous_attribute = getattr(resources_lib, 'entrypoint', None)
        sys.modules['resources.lib.entrypoint'] = entrypoint
        resources_lib.entrypoint = entrypoint
        try:
            self.assertFalse(
                service._refresh_discover_cache(
                    {
                        'url': 'https://discover.provider.plex.tv/hubs/sections/home/test',
                        'kind': 'hub',
                        '_account_hash': 'account-a',
                    }
                )
            )
        finally:
            if previous_module is None:
                sys.modules.pop('resources.lib.entrypoint', None)
            else:
                sys.modules['resources.lib.entrypoint'] = previous_module
            if previous_attribute is None:
                delattr(resources_lib, 'entrypoint')
            else:
                resources_lib.entrypoint = previous_attribute

        self.assertEqual(completed, [False])

    def test_fresh_prefetch_snapshot_is_acknowledged_without_cloud_io(self):
        service_entry = load_service_entry()
        service = object.__new__(service_entry.Service)
        completed = []
        service_entry.app.ACCOUNT = types.SimpleNamespace(authenticated=True)
        service_entry.discover_cache = types.SimpleNamespace(
            account_matches=lambda account_hash: account_hash == "account-a",
            read_xml=lambda *args: ("fresh", [object()]),
            CACHE_FRESH="fresh",
            finish_refresh=lambda request, success: completed.append(success) or True,
        )

        self.assertTrue(
            service._refresh_discover_cache(
                {
                    'url': 'https://discover.provider.plex.tv/hubs/sections/home/test',
                    'kind': 'hub',
                    '_account_hash': 'account-a',
                }
            )
        )
        self.assertEqual(completed, [True])

    def test_catalog_prefetch_is_persisted_by_the_service_at_low_priority(self):
        service_entry = load_service_entry()
        service = object.__new__(service_entry.Service)
        queued = []
        service_entry.discover_cache = types.SimpleNamespace(
            claim_hub_prefetch=lambda: ("https://discover.provider.plex.tv/hubs/sections/home/test",),
            enqueue_refresh=lambda *args, **kwargs: queued.append((args, kwargs)) or True,
            REFRESH_PRIORITY_PREFETCH=1,
        )

        service._queue_discover_prefetch()

        self.assertEqual(
            queued,
            [
                (
                    (
                        'https://discover.provider.plex.tv/hubs/sections/home/test',
                        'hub',
                    ),
                    {'priority': 1},
                )
            ],
        )


if __name__ == '__main__':
    unittest.main()
