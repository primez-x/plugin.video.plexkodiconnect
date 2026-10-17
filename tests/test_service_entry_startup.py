import importlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from threading import Event
from time import monotonic
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# The canonical Watchlist URL (resources.lib.plex_discover.WATCHLIST_URL)
WATCHLIST_URL = (
    'https://discover.provider.plex.tv/library/sections/watchlist/all'
    '?includeGuids=1'
)


def load_real_discover_cache():
    """Import the real discover_cache over the Kodi-free stubs in place."""
    for module_name in ('resources.lib.discover_cache',
                        'resources.lib.plex_discover'):
        sys.modules.pop(module_name, None)
    resources_lib = sys.modules.get('resources.lib')
    for attribute in ('discover_cache', 'plex_discover'):
        if resources_lib is not None and hasattr(resources_lib, attribute):
            delattr(resources_lib, attribute)
    return importlib.import_module('resources.lib.discover_cache')


def load_service_entry():
    module_names = (
        'xbmc',
        'xbmcvfs',
        'resources.lib.service_entry',
        'resources.lib.plex_community',
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

    def test_command_wake_ends_the_idle_service_sleep_early(self):
        service_entry = load_service_entry()
        wake = Event()
        sleeps = []

        def sleep(ms):
            sleeps.append(ms)
            if len(sleeps) == 2:
                # KodiMonitor.onNotification runs between xbmc.sleep slices
                wake.set()

        self.assertTrue(service_entry.service_loop_wait(200, wake, sleep=sleep))
        self.assertEqual(sleeps, [40, 40])
        self.assertFalse(wake.is_set())

    def test_service_sleep_without_wake_keeps_its_full_duration(self):
        service_entry = load_service_entry()
        sleeps = []

        self.assertFalse(
            service_entry.service_loop_wait(200, Event(), sleep=sleeps.append))
        self.assertEqual(sum(sleeps), 200)
        self.assertTrue(all(ms <= 40 for ms in sleeps))

    def test_skip_marker_countdown_keeps_a_single_33ms_sleep(self):
        service_entry = load_service_entry()
        sleeps = []
        wake = Event()
        wake.set()

        self.assertTrue(service_entry.service_loop_wait(
            service_entry.service_loop_sleep_ms(True), wake, sleep=sleeps.append))
        self.assertEqual(sleeps, [33])

    def test_artwork_normalization_runs_in_background_once_per_server(self):
        service_entry = load_service_entry()
        service = object.__new__(service_entry.Service)
        service.artwork_normalized_server = None
        tasks = []

        class FunctionAsTask(object):
            def __init__(self, function, callback, *args):
                self.function = function
                self.args = args

        service_entry.backgroundthread.FunctionAsTask = FunctionAsTask
        service_entry.backgroundthread.BGThreader = types.SimpleNamespace(
            addTask=tasks.append)
        normalized = []
        service_entry.kodi_db.normalize_artwork_urls = normalized.append
        service_entry.app.CONN = types.SimpleNamespace(server=None)

        service._schedule_artwork_normalization()
        self.assertEqual(tasks, [])

        service_entry.app.CONN.server = 'https://192.168.0.10:32400'
        service._schedule_artwork_normalization()
        service._schedule_artwork_normalization()
        self.assertEqual(len(tasks), 1)
        # Mid-session PMS address change: normalize again for the new server
        service_entry.app.CONN.server = 'https://10.0.0.5:32400'
        service._schedule_artwork_normalization()
        self.assertEqual(len(tasks), 2)

        for task in tasks:
            task.function(*task.args)
        self.assertEqual(normalized, [
            'https://192.168.0.10:32400', 'https://10.0.0.5:32400'])

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

    def test_watchlist_refresh_downloads_every_page_as_one_source(self):
        service_entry = load_service_entry()
        service = object.__new__(service_entry.Service)
        completed = []
        stored = []
        projected = []
        epoch = ["before-request"]
        service_entry.plex_membership = types.SimpleNamespace(generation=lambda: epoch[0])
        watchlist_xml = ET.Element("MediaContainer")
        service_entry.app.ACCOUNT = types.SimpleNamespace(authenticated=True)
        service_entry.discover_cache = types.SimpleNamespace(
            account_matches=lambda account_hash: account_hash == "account-a",
            refresh_hosts=lambda cache_kind: ("discover.provider.plex.tv",),
            read_xml=lambda *args: ("stale", [object()]),
            CACHE_FRESH="fresh",
            watchlist_generation=lambda: "generation",
            put_xml=lambda *args, **kwargs: stored.append(args) or True,
            finish_refresh=lambda request, success: completed.append(success) or True,
        )
        entrypoint = types.ModuleType('resources.lib.entrypoint')
        def download_watchlist():
            epoch[0] = "after-mutation"
            return watchlist_xml
        entrypoint._download_watchlist = download_watchlist
        entrypoint._publish_membership = lambda *args, **kwargs: projected.append(kwargs) or True

        def unexpected(*args):
            raise AssertionError("watchlist must use the paged downloader")

        entrypoint._download_provider_xmls = unexpected
        resources_lib = sys.modules['resources.lib']
        previous_module = sys.modules.get('resources.lib.entrypoint')
        previous_attribute = getattr(resources_lib, 'entrypoint', None)
        sys.modules['resources.lib.entrypoint'] = entrypoint
        resources_lib.entrypoint = entrypoint
        url = WATCHLIST_URL
        try:
            self.assertTrue(
                service._refresh_discover_cache(
                    {'url': url, 'kind': 'watchlist', '_account_hash': 'account-a'}
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

        self.assertEqual(stored, [(url, [watchlist_xml], 'watchlist')])
        self.assertEqual(completed, [True])

        self.assertEqual(projected, [{"generation": "before-request"}])

    def test_watchlist_refresh_retries_a_rejected_membership_publication(self):
        for freshness in ("fresh", "stale"):
            with self.subTest(freshness=freshness):
                service_entry = load_service_entry()
                service = object.__new__(service_entry.Service)
                completed = []
                xml = ET.Element("MediaContainer")
                service_entry.app.ACCOUNT = types.SimpleNamespace(authenticated=True)
                service_entry.plex_membership = types.SimpleNamespace(generation=lambda: "before")
                service_entry.discover_cache = types.SimpleNamespace(
                    account_matches=lambda account_hash: account_hash == "account-a",
                    read_xml=lambda *args: (freshness, [xml]),
                    CACHE_FRESH="fresh",
                    watchlist_generation=lambda: "watchlist-generation",
                    put_xml=lambda *args, **kwargs: True,
                    finish_refresh=lambda request, success: completed.append(success) or True,
                )
                entrypoint = types.ModuleType("resources.lib.entrypoint")
                entrypoint._download_watchlist = lambda: xml
                entrypoint._publish_membership = lambda *args, **kwargs: False
                with patch.dict(sys.modules, {"resources.lib.entrypoint": entrypoint}), patch.object(
                    sys.modules["resources.lib"], "entrypoint", entrypoint, create=True
                ):
                    self.assertFalse(service._refresh_discover_cache(
                        {"url": WATCHLIST_URL, "kind": "watchlist", "_account_hash": "account-a"}
                    ))
                self.assertEqual([False], completed)

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

    def test_discover_refresh_scheduler_is_idle_without_a_wake_signal(self):
        service_entry = load_service_entry()
        service = object.__new__(service_entry.Service)
        claims = []
        service.discover_refresh_threader = types.SimpleNamespace(
            working=lambda: False,
            addTask=lambda *args: claims.append(args),
        )
        maintenance_claims = []
        service.discover_maintenance_threader = types.SimpleNamespace(
            working=lambda: False,
            addTask=lambda *args: maintenance_claims.append(args),
        )
        service.next_discover_refresh_fallback = monotonic() + 60
        service.last_discover_refresh_wake = 0
        service.next_discover_maintenance_fallback = monotonic() + 60
        service_entry.app.APP = types.SimpleNamespace(
            discover_refresh_event=Event(),
            discover_maintenance_event=Event())
        service_entry.app.ACCOUNT = types.SimpleNamespace(authenticated=True)
        service_entry.discover_cache = types.SimpleNamespace(
            refresh_requested_at=lambda: 0,
            maintenance_due=lambda: (_ for _ in ()).throw(
                AssertionError('idle scheduler must not inspect maintenance state')),
            claim_refresh=lambda: (_ for _ in ()).throw(
                AssertionError('idle scheduler must not claim refresh work')),
        )

        service._schedule_discover_refresh()
        service._schedule_discover_maintenance()

        self.assertEqual(claims, [])
        self.assertEqual(maintenance_claims, [])

    def test_duplicate_discover_wakes_coalesce_to_one_claim(self):
        service_entry = load_service_entry()
        service = object.__new__(service_entry.Service)
        claims = []
        service.discover_refresh_threader = types.SimpleNamespace(
            working=lambda: False,
            addTask=lambda *args: claims.append(args),
        )
        service.next_discover_refresh_fallback = monotonic() + 60
        service.last_discover_refresh_wake = -1
        event = Event()
        event.set()
        service_entry.app.APP = types.SimpleNamespace(
            discover_refresh_event=event)
        service_entry.app.ACCOUNT = types.SimpleNamespace(authenticated=True)
        service_entry.backgroundthread.FunctionAsTask = lambda *args: args
        service_entry.discover_cache = types.SimpleNamespace(
            refresh_requested_at=lambda: 100,
            claim_hub_prefetch=lambda: (),
            claim_refresh=lambda: {
                'url': 'https://discover.provider.plex.tv/hubs/sections/home/test',
                'kind': 'hub',
            },
            refresh_hosts=lambda kind: (),
        )

        service._schedule_discover_refresh()
        event.set()
        service._schedule_discover_refresh()

        self.assertEqual(len(claims), 1)

    def test_slow_fallback_recovers_a_missed_discover_notification(self):
        service_entry = load_service_entry()
        service = object.__new__(service_entry.Service)
        claims = []
        service.discover_refresh_threader = types.SimpleNamespace(
            working=lambda: False,
            addTask=lambda *args: claims.append(args),
        )
        service.next_discover_refresh_fallback = monotonic() - 1
        service.last_discover_refresh_wake = 0
        service_entry.app.APP = types.SimpleNamespace(
            discover_refresh_event=Event())
        service_entry.app.ACCOUNT = types.SimpleNamespace(authenticated=True)
        service_entry.backgroundthread.FunctionAsTask = lambda *args: args
        service_entry.discover_cache = types.SimpleNamespace(
            refresh_requested_at=lambda: 0,
            claim_hub_prefetch=lambda: (),
            claim_refresh=lambda: {
                'url': 'https://discover.provider.plex.tv/hubs/sections/home/test',
                'kind': 'hub',
            },
        )

        service._schedule_discover_refresh()

        self.assertEqual(len(claims), 1)

    def _watchlist_warm_service(self, window=None, playing=False,
                                authenticated=True, enqueued=True):
        service_entry = load_service_entry()
        service = object.__new__(service_entry.Service)
        service.next_watchlist_warm = 0.0
        properties = {'plex_token': 'token', 'plex_restricteduser': 'false'}
        properties.update(window or {})
        service_entry.utils.window = lambda key: properties.get(key, '')
        service_entry.app.ACCOUNT = types.SimpleNamespace(
            authenticated=authenticated)
        service_entry.app.APP = types.SimpleNamespace(is_playing=playing)
        service_entry.plex_community = types.SimpleNamespace(selected_list=lambda: None)
        queued = []
        service_entry.discover_cache = types.SimpleNamespace(
            WATCHLIST_URL=WATCHLIST_URL,
            REFRESH_PRIORITY_PREFETCH=1,
            enqueue_refresh=lambda *args, **kwargs: queued.append(
                (args, kwargs)) or enqueued,
        )
        clock = {'now': 1000.0}
        service_entry.monotonic = lambda: clock['now']
        return service_entry, service, queued, clock, properties

    def test_watchlist_is_warmed_after_sign_in_and_every_ten_minutes(self):
        service_entry, service, queued, clock, _ = self._watchlist_warm_service()

        service._schedule_watchlist_warm()
        clock['now'] += service_entry.WATCHLIST_WARM_INTERVAL_SECONDS - 1
        service._schedule_watchlist_warm()
        self.assertEqual(
            queued,
            [((WATCHLIST_URL, 'watchlist'), {'priority': 1})])

        clock['now'] += 1
        service._schedule_watchlist_warm()
        self.assertEqual(len(queued), 2)
        self.assertEqual(service_entry.WATCHLIST_WARM_INTERVAL_SECONDS, 600)

    def test_saved_favourite_list_warms_without_a_provider_directory(self):
        service_entry, service, queued, clock, _ = self._watchlist_warm_service()
        service_entry.plex_community.selected_list = lambda: {'id': 'selected-list'}

        service._schedule_watchlist_warm()
        self.assertEqual(queued, [
            ((WATCHLIST_URL, 'watchlist'), {'priority': 1}),
            (('https://community.plex.tv/api/favourites', 'community_membership'), {'priority': 1}),
        ])
        clock['now'] += service_entry.WATCHLIST_WARM_INTERVAL_SECONDS - 1
        service._schedule_watchlist_warm()
        self.assertEqual(len(queued), 2)

    def test_failed_favourite_enqueue_uses_the_existing_retry_schedule(self):
        service_entry, service, queued, clock, _ = self._watchlist_warm_service()
        service_entry.plex_community.selected_list = lambda: {'id': 'selected-list'}

        def enqueue(*args, **kwargs):
            queued.append((args, kwargs))
            return args[1] != 'community_membership'

        service_entry.discover_cache.enqueue_refresh = enqueue
        service._schedule_watchlist_warm()
        clock['now'] += service_entry.DISCOVER_WAKE_FALLBACK_SECONDS
        service._schedule_watchlist_warm()
        self.assertEqual(len(queued), 4)

    def test_watchlist_warm_up_queues_the_url_the_listing_caches(self):
        service_entry, service, _, _, _ = self._watchlist_warm_service()
        cache = load_real_discover_cache()
        queued = []
        cache.enqueue_refresh = lambda *args, **kwargs: queued.append(
            (args, kwargs)) or True
        service_entry.discover_cache = cache

        service._schedule_watchlist_warm()

        self.assertEqual(
            queued,
            [((WATCHLIST_URL, 'watchlist'),
              {'priority': cache.REFRESH_PRIORITY_PREFETCH})])
        self.assertIs(cache.WATCHLIST_URL, cache.plex_discover.WATCHLIST_URL)
        # The refresh worker accepts it, so the warm-up fills the same entry
        # the Watchlist listing and the membership hints read
        self.assertTrue(cache._valid_refresh_url(WATCHLIST_URL, 'watchlist'))

    def test_watchlist_warm_waits_for_sign_in_and_playback_to_end(self):
        for options in (
                {'window': {'plex_token': ''}},
                {'window': {'plex_restricteduser': 'true'}},
                {'authenticated': False},
                {'playing': True}):
            service_entry, service, queued, clock, _ = \
                self._watchlist_warm_service(**options)

            service._schedule_watchlist_warm()
            self.assertEqual(queued, [], options)

        # Due work runs shortly after playback ends
        service_entry.app.APP.is_playing = False
        clock['now'] += service_entry.WATCHLIST_WARM_RECHECK_SECONDS
        service._schedule_watchlist_warm()
        self.assertEqual(len(queued), 1)

    def test_sign_in_warms_the_new_users_watchlist_right_away(self):
        service_entry, service, queued, clock, _ = self._watchlist_warm_service()
        service._schedule_watchlist_warm()
        service._do_auth = lambda: True
        service.welcome_msg = False
        service_entry.app.reload = lambda: None
        service_entry.app.APP.resume_threads = lambda: None

        service.authenticate()
        service._schedule_watchlist_warm()

        self.assertEqual(len(queued), 2)

    def test_failed_watchlist_warm_enqueue_retries_on_the_slow_fallback(self):
        service_entry, service, queued, clock, _ = \
            self._watchlist_warm_service(enqueued=False)

        service._schedule_watchlist_warm()
        clock['now'] += service_entry.DISCOVER_WAKE_FALLBACK_SECONDS - 1
        service._schedule_watchlist_warm()
        self.assertEqual(len(queued), 1)
        clock['now'] += 1
        service._schedule_watchlist_warm()
        self.assertEqual(len(queued), 2)


if __name__ == '__main__':
    unittest.main()
