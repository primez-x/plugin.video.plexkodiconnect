import importlib
import sys
import types
import unittest
from pathlib import Path
from urllib.parse import parse_qsl, urlencode


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class FakeResponse(object):
    def __init__(self, payload=None, ok=True, status_code=200):
        self.payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class DownloadRecorder(object):
    def __init__(self, responses):
        self.calls = []
        self.responses = list(responses)

    def downloadUrl(self, url, *args, **kwargs):
        self.calls.append((url, args, kwargs))
        return self.responses.pop(0)


def load_service_entry():
    module_names = (
        "xbmc",
        "xbmcvfs",
        "resources.lib.service_entry",
        "resources.lib.discover_cache",
        "resources.lib.plex_discover",
        "resources.lib.utils",
        "resources.lib.clientinfo",
        "resources.lib.initialsetup",
        "resources.lib.kodimonitor",
        "resources.lib.sync",
        "resources.lib.library_sync",
        "resources.lib.websocket_client",
        "resources.lib.kodi_db",
        "resources.lib.plex_db",
        "resources.lib.plex_companion",
        "resources.lib.plex_functions",
        "resources.lib.playback_starter",
        "resources.lib.variables",
        "resources.lib.app",
        "resources.lib.loghandler",
        "resources.lib.backgroundthread",
        "resources.lib.skip_plex_markers",
        "resources.lib.downloadutils",
        "resources.lib.windows",
        "resources.lib.windows.userselect",
    )
    for module_name in module_names:
        sys.modules.pop(module_name, None)
    sys.modules.pop("resources.lib.watchlist", None)
    resources_lib = sys.modules.get("resources.lib")
    if resources_lib is not None:
        for attribute in ("watchlist", "discover_cache"):
            if hasattr(resources_lib, attribute):
                delattr(resources_lib, attribute)

    xbmc = types.ModuleType("xbmc")
    xbmc.commands = []
    xbmc.executebuiltin = xbmc.commands.append
    xbmc.sleep = lambda milliseconds: None
    sys.modules["xbmc"] = xbmc
    sys.modules["xbmcvfs"] = types.ModuleType("xbmcvfs")

    modules = {}
    for module_name in module_names[4:]:
        modules[module_name] = types.ModuleType(module_name)
        sys.modules[module_name] = modules[module_name]

    notifications = []
    window_properties = {}
    utils = modules["resources.lib.utils"]
    utils.parse_qsl = parse_qsl

    def window(key, value=None, clear=False):
        if key == "plex_token" and value is None:
            return "plex-token"
        if clear:
            window_properties.pop(key, None)
            return None
        if value is not None:
            window_properties[key] = value
            return None
        return window_properties.get(key, "")

    utils.window = window
    utils.lang = lambda string_id: "PlexKodiConnect"
    utils.dialog = lambda *args, **kwargs: notifications.append((args, kwargs))

    def get_headers(options=None, include_token=True):
        headers = {"Accept": "*/*", "X-Plex-Product": "PlexKodiConnect"}
        headers.update(options or {})
        return headers

    modules["resources.lib.clientinfo"].getXArgsDeviceInfo = get_headers
    modules["resources.lib.loghandler"].config = lambda: None
    modules["resources.lib.app"].CONN = object()
    modules["resources.lib.app"].init = lambda entrypoint=False: None
    modules["resources.lib.windows"].userselect = modules[
        "resources.lib.windows.userselect"
    ]

    service_entry = importlib.import_module("resources.lib.service_entry")
    return service_entry, xbmc, notifications, window_properties


def service_proxy(service_entry):
    class ServiceProxy(object):
        def watchlist_modify_tmdb(self, api_type, raw_params):
            return service_entry.Service.watchlist_modify_tmdb(
                self, api_type, raw_params
            )

        def watchlist_modify_key(self, api_type, raw_params):
            return service_entry.Service.watchlist_modify_key(
                self, api_type, raw_params
            )

    return ServiceProxy()


class TmdbWatchlistTests(unittest.TestCase):
    MOVIE_KEY = "5d7768244de0ee001fcc7fed"
    THE_ODYSSEY_ID = "1368337"
    THE_ODYSSEY_KEY = "670cb74204e43056959bd4b3"

    def _metadata_payload(self):
        return {
            "MediaContainer": {
                "Metadata": {
                    "type": "movie",
                    "guid": "plex://movie/%s" % self.MOVIE_KEY,
                    "ratingKey": self.MOVIE_KEY,
                }
            }
        }

    def _watchlist_state_payload(self, rating_key, watchlisted):
        user_state = []
        if watchlisted:
            user_state.append({"ratingKey": rating_key, "watchlistedAt": 1})
        return {"MediaContainer": {"UserState": user_state}}

    def test_tmdb_watchlist_action_resolves_exact_id_then_mutates(self):
        service_entry, xbmc, notifications, _ = load_service_entry()
        downloader = DownloadRecorder(
            [
                FakeResponse(self._metadata_payload()),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, False)),
                FakeResponse(),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, True)),
            ]
        )
        service_entry.watchlist.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_tmdb(
            service_proxy(service_entry), "tmdb_id=603&tmdb_type=movie"
        )

        self.assertTrue(result)
        self.assertEqual(notifications, [])
        self.assertEqual(xbmc.commands, ["Container.Refresh"])
        self.assertEqual(len(downloader.calls), 4)

        metadata_url, _, metadata_kwargs = downloader.calls[0]
        self.assertEqual(
            metadata_url, service_entry.watchlist.plex_discover.METADATA_MATCH_URL
        )
        self.assertEqual(
            metadata_kwargs["parameters"], {"guid": "tmdb://603", "type": 1}
        )
        self.assertEqual(metadata_kwargs["headerOptions"]["Accept"], "application/json")
        self.assertEqual(
            metadata_kwargs["headerOptions"]["X-Plex-Product"], "PlexKodiConnect"
        )
        self.assertTrue(metadata_kwargs["return_response"])
        self.assertFalse(metadata_kwargs["authenticate"])

        state_url, _, state_kwargs = downloader.calls[1]
        self.assertEqual(
            state_url,
            service_entry.watchlist.WATCHLIST_USER_STATE_URL % self.MOVIE_KEY,
        )
        self.assertEqual(state_kwargs["headerOptions"]["Accept"], "application/json")

        action_url, _, action_kwargs = downloader.calls[2]
        self.assertEqual(
            action_url,
            "https://discover.provider.plex.tv/actions/addToWatchlist",
        )
        self.assertEqual(action_kwargs["action_type"], "PUT")
        self.assertEqual(action_kwargs["parameters"], {"ratingKey": self.MOVIE_KEY})
        self.assertTrue(action_kwargs["return_response"])
        self.assertFalse(action_kwargs["authenticate"])

    def test_failed_action_does_not_refresh_or_claim_success(self):
        service_entry, xbmc, notifications, _ = load_service_entry()
        service_entry.watchlist.WATCHLIST_VERIFY_ATTEMPTS = 1
        downloader = DownloadRecorder(
            [
                FakeResponse(self._metadata_payload()),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, False)),
                FakeResponse(ok=False, status_code=500),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, False)),
            ]
        )
        service_entry.watchlist.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_tmdb(
            service_proxy(service_entry), "tmdb_id=603&tmdb_type=movie"
        )

        self.assertFalse(result)
        self.assertEqual(xbmc.commands, [])
        self.assertEqual(len(downloader.calls), 4)
        self.assertEqual(notifications[0][0][2], "Plex Watchlist could not be updated.")

    def test_http_ok_without_the_desired_state_is_a_failure(self):
        service_entry, xbmc, notifications, _ = load_service_entry()
        service_entry.watchlist.WATCHLIST_VERIFY_ATTEMPTS = 1
        downloader = DownloadRecorder(
            [
                FakeResponse(self._metadata_payload()),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, False)),
                FakeResponse(),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, False)),
            ]
        )
        service_entry.watchlist.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_tmdb(
            service_proxy(service_entry), "tmdb_id=603&tmdb_type=movie"
        )

        self.assertFalse(result)
        self.assertEqual(xbmc.commands, [])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0][0][2], "Plex Watchlist could not be updated.")

    def test_existing_desired_state_is_silently_confirmed_without_a_put(self):
        service_entry, xbmc, notifications, _ = load_service_entry()
        downloader = DownloadRecorder(
            [
                FakeResponse(self._metadata_payload()),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, True)),
            ]
        )
        service_entry.watchlist.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_tmdb(
            service_proxy(service_entry), "tmdb_id=603&tmdb_type=movie"
        )

        self.assertTrue(result)
        self.assertEqual(len(downloader.calls), 2)
        self.assertEqual(notifications, [])
        self.assertEqual(xbmc.commands, ["Container.Refresh"])

    def test_remove_verifies_that_the_item_is_absent(self):
        service_entry, xbmc, notifications, _ = load_service_entry()
        downloader = DownloadRecorder(
            [
                FakeResponse(self._metadata_payload()),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, True)),
                FakeResponse(),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, False)),
            ]
        )
        service_entry.watchlist.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_remove_tmdb(
            service_proxy(service_entry), "tmdb_id=603&tmdb_type=movie"
        )

        self.assertTrue(result)
        self.assertEqual(notifications, [])
        self.assertEqual(xbmc.commands, ["Container.Refresh"])
        self.assertEqual(
            downloader.calls[2][0],
            "https://discover.provider.plex.tv/actions/removeFromWatchlist",
        )

    def test_optimistic_request_commits_only_after_authoritative_confirmation(self):
        service_entry, xbmc, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        downloader = DownloadRecorder(
            [
                FakeResponse(self._metadata_payload()),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, False)),
                FakeResponse(),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, True)),
            ]
        )
        watchlist.downloadutils.DownloadUtils = lambda: downloader

        result = watchlist.set_tmdb(
            {"tmdb_id": "603", "tmdb_type": "movie"}, "present"
        )

        self.assertTrue(result)
        self.assertEqual(notifications, [])
        self.assertEqual(xbmc.commands, [])
        self.assertEqual(properties[watchlist.DETAIL_STATE], "present")
        self.assertNotIn(watchlist.DETAIL_PENDING, properties)
        self.assertNotIn(watchlist.DETAIL_REQUEST_ID, properties)
        self.assertEqual(
            properties[watchlist._item_state_property(identity)], "present"
        )

    def test_direct_tmdb_projection_begins_before_metadata_resolution(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        observed_before_resolution = []

        def discover(tmdb_id, tmdb_type):
            observed_before_resolution.append(
                (
                    tmdb_id,
                    tmdb_type,
                    properties.get(watchlist.DETAIL_STATE),
                    properties.get(watchlist.DETAIL_PENDING),
                    properties.get(watchlist.DETAIL_REVISION),
                )
            )
            return self.MOVIE_KEY

        watchlist.discover_tmdb_ratingkey = discover
        watchlist.change = lambda api_type, rating_key: (True, True)

        self.assertTrue(
            watchlist.set_tmdb({"tmdb_id": "603", "tmdb_type": "movie"}, "present")
        )
        self.assertEqual(len(observed_before_resolution), 1)
        _, _, state_name, pending, revision = observed_before_resolution[0]
        self.assertEqual(state_name, "present")
        self.assertTrue(pending)
        self.assertEqual(pending, revision)
        self.assertNotIn(watchlist.DETAIL_PENDING, properties)
        self.assertEqual(properties[watchlist.DETAIL_STATE], "present")
        self.assertEqual(notifications, [])

    def test_detail_tmdb_worker_runs_after_the_foreground_projection(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        properties[watchlist.DETAIL_OPTIMISTIC_STATE] = "present"

        request = watchlist.begin_tmdb(
            {"tmdb_id": "603", "tmdb_type": "movie"}, "present"
        )

        self.assertEqual(request["watchlist_identity"], identity)
        self.assertEqual(request["desired"], "present")
        self.assertEqual(properties[watchlist.DETAIL_STATE], "present")
        self.assertEqual(properties[watchlist.DETAIL_PENDING], request["request_id"])
        self.assertEqual(properties[watchlist.DETAIL_OPTIMISTIC_STATE], "present")

        downloader = DownloadRecorder(
            [
                FakeResponse(self._metadata_payload()),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, False)),
                FakeResponse(),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, True)),
            ]
        )
        watchlist.downloadutils.DownloadUtils = lambda: downloader

        self.assertTrue(
            service_entry.Service.watchlist_detail_tmdb(
                service_proxy(service_entry), urlencode(request)
            )
        )
        self.assertEqual(len(downloader.calls), 4)
        self.assertEqual(properties[watchlist.DETAIL_STATE], "present")
        self.assertNotIn(watchlist.DETAIL_PENDING, properties)
        self.assertNotIn(watchlist.DETAIL_OPTIMISTIC_STATE, properties)
        self.assertEqual(notifications, [])

    def test_detail_worker_commits_after_the_detail_dialog_closes(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.THE_ODYSSEY_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity
        properties[watchlist.DETAIL_OPTIMISTIC_STATE] = "present"
        properties[watchlist.DETAIL_OPTIMISTIC_IDENTITY] = identity
        request = watchlist.begin_key(
            {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "present"
        )
        for property_name in (
            watchlist.DETAIL_IDENTITY,
            watchlist.DETAIL_STATE,
            watchlist.DETAIL_PREVIOUS_STATE,
            watchlist.DETAIL_PENDING,
            watchlist.DETAIL_REQUEST_ID,
            watchlist.DETAIL_REVISION,
            watchlist.DETAIL_OPTIMISTIC_STATE,
            watchlist.DETAIL_OPTIMISTIC_IDENTITY,
        ):
            properties.pop(property_name, None)
        changed = []
        watchlist.change = lambda *args: changed.append(args) or (True, True)

        self.assertTrue(watchlist.complete_key(request))
        self.assertEqual(
            changed, [("addToWatchlist", self.THE_ODYSSEY_KEY)]
        )
        self.assertEqual(
            properties[watchlist._item_state_property(identity)], "present"
        )
        self.assertIsNone(watchlist._current_mutation_intent(identity))
        self.assertEqual(
            properties[watchlist._item_mutation_completed_property(identity)],
            request["request_id"],
        )
        self.assertNotIn(watchlist.DETAIL_STATE, properties)
        self.assertEqual(notifications, [])

    def test_newer_detail_request_supersedes_a_queued_worker(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.THE_ODYSSEY_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity
        first_request = watchlist.begin_key(
            {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "present"
        )
        for property_name in (
            watchlist.DETAIL_IDENTITY,
            watchlist.DETAIL_STATE,
            watchlist.DETAIL_PREVIOUS_STATE,
            watchlist.DETAIL_PENDING,
            watchlist.DETAIL_REQUEST_ID,
            watchlist.DETAIL_REVISION,
        ):
            properties.pop(property_name, None)
        properties[watchlist.DETAIL_IDENTITY] = identity
        second_request = watchlist.begin_key(
            {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "absent"
        )
        changed = []
        watchlist.change = lambda *args: changed.append(args) or (True, False)

        self.assertTrue(watchlist.complete_key(first_request))
        self.assertTrue(watchlist.complete_key(second_request))
        self.assertEqual(
            changed, [("removeFromWatchlist", self.THE_ODYSSEY_KEY)]
        )
        self.assertEqual(
            properties[watchlist._item_state_property(identity)], "absent"
        )
        self.assertEqual(notifications, [])

    def test_worker_reconciles_a_newer_intent_arriving_during_plex_change(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.THE_ODYSSEY_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity
        first_request = watchlist.begin_key(
            {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "present"
        )
        changed = []

        def change(api_type, rating_key):
            changed.append((api_type, rating_key))
            if api_type == "addToWatchlist":
                watchlist.begin_key(
                    {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "absent"
                )
                return True, True
            return True, False

        watchlist.change = change

        self.assertTrue(watchlist.complete_key(first_request))
        self.assertEqual(
            changed,
            [
                ("addToWatchlist", self.THE_ODYSSEY_KEY),
                ("removeFromWatchlist", self.THE_ODYSSEY_KEY),
            ],
        )
        self.assertEqual(properties[watchlist.DETAIL_STATE], "absent")
        self.assertNotIn(watchlist.DETAIL_PENDING, properties)
        self.assertIsNone(watchlist._current_mutation_intent(identity))
        self.assertEqual(notifications, [])

    def test_stale_worker_failure_is_silent_when_a_newer_intent_succeeds(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.THE_ODYSSEY_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity
        first_request = watchlist.begin_key(
            {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "present"
        )
        changed = []

        def change(api_type, rating_key):
            changed.append((api_type, rating_key))
            if api_type == "addToWatchlist":
                watchlist.begin_key(
                    {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "absent"
                )
                return False, None
            return True, False

        watchlist.change = change

        self.assertTrue(watchlist.complete_key(first_request))
        self.assertEqual(
            changed,
            [
                ("addToWatchlist", self.THE_ODYSSEY_KEY),
                ("removeFromWatchlist", self.THE_ODYSSEY_KEY),
            ],
        )
        self.assertEqual(properties[watchlist.DETAIL_STATE], "absent")
        self.assertEqual(notifications, [])

    def test_detail_worker_failure_reverts_and_clears_skin_projection(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.THE_ODYSSEY_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity
        properties[watchlist.DETAIL_STATE] = "absent"
        properties[watchlist.DETAIL_OPTIMISTIC_STATE] = "present"
        request = watchlist.begin_key(
            {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "present"
        )
        watchlist.change = lambda api_type, rating_key: (False, None)

        self.assertFalse(watchlist.complete_key(request))
        self.assertEqual(properties[watchlist.DETAIL_STATE], "absent")
        self.assertNotIn(watchlist.DETAIL_PENDING, properties)
        self.assertNotIn(watchlist.DETAIL_OPTIMISTIC_STATE, properties)
        self.assertEqual(notifications[0][0][2], "Plex Watchlist could not be updated.")

    def test_duplicate_mutation_is_ignored_while_the_detail_is_pending(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        direct_identity = "plex.%s" % self.THE_ODYSSEY_KEY
        tmdb_identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = direct_identity
        properties[watchlist.DETAIL_PENDING] = "in-flight"
        changed = []
        watchlist.change = lambda *args: changed.append(args) or (True, True)

        self.assertFalse(
            watchlist.set_key(
                {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"},
                "present",
            )
        )
        self.assertEqual(changed, [])

        properties[watchlist.DETAIL_IDENTITY] = tmdb_identity
        self.assertFalse(
            watchlist.set_tmdb({"tmdb_id": "603", "tmdb_type": "movie"}, "present")
        )
        self.assertEqual(changed, [])
        self.assertEqual(notifications, [])

    def test_declined_begin_clears_only_its_matching_skin_projection(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.THE_ODYSSEY_KEY
        properties[watchlist.DETAIL_IDENTITY] = "plex.newer-item"
        properties[watchlist.DETAIL_OPTIMISTIC_STATE] = "present"
        properties[watchlist.DETAIL_OPTIMISTIC_IDENTITY] = identity

        self.assertIsNone(
            watchlist.begin_key(
                {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"},
                "present",
            )
        )
        self.assertNotIn(watchlist.DETAIL_OPTIMISTIC_STATE, properties)
        self.assertNotIn(watchlist.DETAIL_OPTIMISTIC_IDENTITY, properties)
        self.assertEqual(notifications, [])

    def test_rapid_begin_replaces_the_pending_detail_intent(self):
        service_entry, _, notifications, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.THE_ODYSSEY_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity
        properties[watchlist.DETAIL_PENDING] = "in-flight"
        properties[watchlist.DETAIL_OPTIMISTIC_STATE] = "present"
        properties[watchlist.DETAIL_OPTIMISTIC_IDENTITY] = identity

        request = watchlist.begin_key(
            {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "present"
        )
        self.assertIsNotNone(request)
        self.assertEqual(properties[watchlist.DETAIL_PENDING], request["request_id"])
        self.assertEqual(properties[watchlist.DETAIL_STATE], "present")
        self.assertEqual(properties[watchlist.DETAIL_OPTIMISTIC_STATE], "present")
        self.assertEqual(properties[watchlist.DETAIL_OPTIMISTIC_IDENTITY], identity)
        self.assertEqual(notifications, [])

    def test_legacy_set_key_mutates_without_an_active_detail(self):
        service_entry, _, notifications, _ = load_service_entry()
        watchlist = service_entry.watchlist
        changed = []
        watchlist.change = lambda *args: changed.append(args) or (True, True)

        self.assertTrue(
            watchlist.set_key(
                {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"},
                "present",
            )
        )
        self.assertEqual(
            changed, [("addToWatchlist", self.THE_ODYSSEY_KEY)]
        )
        self.assertEqual(notifications, [])

    def test_legacy_set_tmdb_mutates_without_an_active_detail(self):
        service_entry, _, notifications, _ = load_service_entry()
        watchlist = service_entry.watchlist
        changed = []
        watchlist.discover_tmdb_ratingkey = lambda tmdb_id, tmdb_type: self.MOVIE_KEY
        watchlist.change = lambda *args: changed.append(args) or (True, True)

        self.assertTrue(
            watchlist.set_tmdb({"tmdb_id": "603", "tmdb_type": "movie"}, "present")
        )
        self.assertEqual(changed, [("addToWatchlist", self.MOVIE_KEY)])
        self.assertEqual(notifications, [])

    def test_action_marks_the_detail_pending_before_its_revision(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        writes = []
        original_window = watchlist.utils.window

        def recording_window(key, value=None, clear=False):
            if value is not None:
                writes.append(key)
            return original_window(key, value=value, clear=clear)

        watchlist.utils.window = recording_window
        try:
            request_id, _ = watchlist._begin(identity, "present")
        finally:
            watchlist.utils.window = original_window

        self.assertTrue(request_id)
        self.assertLess(
            writes.index(watchlist.DETAIL_PENDING),
            writes.index(watchlist.DETAIL_REVISION),
        )
        self.assertLess(
            writes.index(watchlist._item_mutation_desired_property(identity)),
            writes.index(watchlist._item_mutation_request_property(identity)),
        )
        self.assertLess(
            writes.index(watchlist._item_mutation_previous_state_property(identity)),
            writes.index(watchlist._item_mutation_request_property(identity)),
        )

    def test_status_snapshots_mutation_revision_before_its_pending_check(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        reads = []
        original_window = watchlist.utils.window

        def recording_window(key, value=None, clear=False):
            if value is None and not clear:
                reads.append(key)
            return original_window(key, value=value, clear=clear)

        watchlist.utils.window = recording_window
        try:
            self.assertIsNotNone(watchlist._capture_status(identity))
        finally:
            watchlist.utils.window = original_window

        self.assertLess(
            reads.index(watchlist.DETAIL_REVISION),
            reads.index(watchlist.DETAIL_PENDING),
        )

    def test_status_probe_cannot_overwrite_the_skin_optimistic_projection(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        properties[watchlist.DETAIL_REVISION] = "before-action"
        properties[watchlist.DETAIL_OPTIMISTIC_STATE] = "present"

        self.assertIsNone(watchlist._capture_status(identity))

        properties.pop(watchlist.DETAIL_OPTIMISTIC_STATE)
        status_revision, mutation_revision = watchlist._capture_status(identity)
        properties[watchlist.DETAIL_OPTIMISTIC_STATE] = "present"

        self.assertFalse(
            watchlist._project_status(
                identity, status_revision, mutation_revision, False
            )
        )

    def test_status_probe_cannot_overwrite_a_newer_mutation(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        properties[watchlist.DETAIL_STATE] = "unknown"
        properties[watchlist.DETAIL_REVISION] = "before-action"

        status_revision, mutation_revision = watchlist._capture_status(identity)
        request_id, _ = watchlist._begin(identity, "present")

        self.assertFalse(
            watchlist._project_status(
                identity, status_revision, mutation_revision, False
            )
        )
        self.assertTrue(watchlist._project_mutation(identity, request_id, True))

        self.assertFalse(
            watchlist._project_status(
                identity, status_revision, mutation_revision, False
            )
        )
        self.assertEqual(properties[watchlist.DETAIL_STATE], "present")
        self.assertEqual(
            properties[watchlist._item_state_property(identity)], "present"
        )

    def test_tmdb_status_reuses_the_active_canonical_rating_key(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        properties[watchlist.DETAIL_RATING_KEY] = self.MOVIE_KEY
        downloader = DownloadRecorder(
            [FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, True))]
        )
        watchlist.downloadutils.DownloadUtils = lambda: downloader

        self.assertTrue(
            watchlist.status_tmdb({"tmdb_id": "603", "tmdb_type": "movie"})
        )

        self.assertEqual(len(downloader.calls), 1)
        self.assertEqual(
            downloader.calls[0][0],
            watchlist.WATCHLIST_USER_STATE_URL % self.MOVIE_KEY,
        )
        self.assertEqual(properties[watchlist.DETAIL_STATE], "present")

    def test_cached_status_is_projected_before_authoritative_refresh(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.MOVIE_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity
        watchlist._remember_state(identity, "present")
        projections = []
        original_project = watchlist._project_status

        def recording_project(identity_arg, status_revision, mutation_revision, observed_state):
            projections.append(observed_state)
            return original_project(
                identity_arg, status_revision, mutation_revision, observed_state
            )

        watchlist._project_status = recording_project
        watchlist.state = lambda rating_key: False
        try:
            self.assertTrue(
                watchlist.status_key(
                    {"rating_key": self.MOVIE_KEY, "plex_type": "movie"}
                )
            )
        finally:
            watchlist._project_status = original_project

        self.assertEqual(projections, [True, False])
        self.assertEqual(properties[watchlist.DETAIL_STATE], "absent")

    def test_status_bootstrap_restores_cached_state_without_network(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        watchlist._remember_state(identity, "present")
        watchlist.state = lambda rating_key: self.fail(
            "bootstrap must not use Plex I/O"
        )

        self.assertTrue(
            watchlist.bootstrap_status_tmdb(
                {"tmdb_id": "603", "tmdb_type": "movie"}
            )
        )

        self.assertEqual(properties[watchlist.DETAIL_STATE], "present")

    def test_reopened_detail_rejects_a_stale_status_from_the_prior_dialog(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "tmdb.movie.603"
        properties[watchlist.DETAIL_IDENTITY] = identity
        properties[watchlist.DETAIL_STATE] = "unknown"
        properties[watchlist.DETAIL_REVISION] = "old-mutation"
        old_status_revision, old_mutation_revision = watchlist._capture_status(
            identity
        )

        # The previous dialog unloads, then the same title opens again.
        properties.pop(watchlist.DETAIL_IDENTITY)
        properties.pop(watchlist.DETAIL_REVISION)
        properties.pop(watchlist.DETAIL_STATUS_REVISION)
        properties[watchlist.DETAIL_IDENTITY] = identity

        new_status_revision, _ = watchlist._capture_status(identity)
        self.assertNotEqual(new_status_revision, old_status_revision)

        self.assertFalse(
            watchlist._project_status(
                identity, old_status_revision, old_mutation_revision, False
            )
        )
        self.assertEqual(properties[watchlist.DETAIL_STATE], "unknown")
        self.assertNotIn(watchlist._item_state_property(identity), properties)

    def test_reopened_detail_restores_a_pending_item_intent_before_status(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.THE_ODYSSEY_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity
        request = watchlist.begin_key(
            {"rating_key": self.THE_ODYSSEY_KEY, "plex_type": "movie"}, "present"
        )

        for property_name in (
            watchlist.DETAIL_IDENTITY,
            watchlist.DETAIL_STATE,
            watchlist.DETAIL_PREVIOUS_STATE,
            watchlist.DETAIL_PENDING,
            watchlist.DETAIL_REQUEST_ID,
            watchlist.DETAIL_REVISION,
            watchlist.DETAIL_STATUS_REVISION,
        ):
            properties.pop(property_name, None)
        properties[watchlist.DETAIL_IDENTITY] = identity
        properties[watchlist.DETAIL_STATE] = "unknown"

        self.assertIsNone(watchlist._capture_status(identity))
        self.assertEqual(properties[watchlist.DETAIL_STATE], "present")
        self.assertEqual(properties[watchlist.DETAIL_PENDING], request["request_id"])
        self.assertEqual(properties[watchlist.DETAIL_REQUEST_ID], request["request_id"])
        self.assertEqual(properties[watchlist.DETAIL_REVISION], request["request_id"])

    def test_monitor_status_requires_current_tmdb_helper_details(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        watchlist.WATCHLIST_MONITOR_ATTEMPTS = 1
        properties[watchlist.DETAIL_CONTEXT_LABEL] = "Current Title"
        properties[watchlist.DETAIL_CONTEXT_DBTYPE] = "movie"
        properties[watchlist.TMDB_MONITOR_ID] = "603"
        properties[watchlist.TMDB_MONITOR_TYPE] = "movie"
        properties[watchlist.TMDB_MONITOR_LABEL] = "Previous Title"
        properties[watchlist.TMDB_MONITOR_DBTYPE] = "movie"

        self.assertFalse(watchlist.status_monitor())
        self.assertNotIn(watchlist.DETAIL_IDENTITY, properties)

        properties[watchlist.TMDB_MONITOR_LABEL] = "Current Title"
        calls = []
        watchlist.status_tmdb = lambda params: calls.append(params) or True
        writes = []
        original_window = watchlist.utils.window

        def recording_window(key, value=None, clear=False):
            if value is not None:
                writes.append(key)
            return original_window(key, value=value, clear=clear)

        watchlist.utils.window = recording_window
        try:
            self.assertTrue(watchlist.status_monitor())
        finally:
            watchlist.utils.window = original_window

        self.assertLess(
            writes.index(watchlist.DETAIL_IDENTITY),
            writes.index(watchlist.DETAIL_TMDB_ID),
        )
        self.assertLess(
            writes.index(watchlist.DETAIL_TMDB_ID),
            writes.index(watchlist.DETAIL_TMDB_TYPE),
        )
        self.assertEqual(properties[watchlist.DETAIL_TMDB_ID], "603")
        self.assertEqual(properties[watchlist.DETAIL_TMDB_TYPE], "movie")
        self.assertEqual(properties[watchlist.DETAIL_IDENTITY], "tmdb.movie.603")
        self.assertEqual(
            calls,
            [
                {
                    "tmdb_id": "603",
                    "tmdb_type": "movie",
                    "watchlist_identity": "tmdb.movie.603",
                }
            ],
        )

    def test_direct_discovery_rating_key_uses_the_same_checked_action(self):
        service_entry, xbmc, notifications, _ = load_service_entry()
        downloader = DownloadRecorder(
            [
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, False)),
                FakeResponse(),
                FakeResponse(self._watchlist_state_payload(self.MOVIE_KEY, True)),
            ]
        )
        service_entry.watchlist.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_key(
            service_proxy(service_entry),
            "rating_key=%s&plex_type=movie" % self.MOVIE_KEY,
        )

        self.assertTrue(result)
        self.assertEqual(notifications, [])
        self.assertEqual(xbmc.commands, ["Container.Refresh"])
        self.assertEqual(len(downloader.calls), 3)
        self.assertEqual(
            downloader.calls[1][2]["parameters"], {"ratingKey": self.MOVIE_KEY}
        )

    def test_clip_rating_keys_are_rejected_before_any_watchlist_mutation(self):
        service_entry, xbmc, notifications, _ = load_service_entry()
        changed = []
        service_entry.watchlist.change = lambda *args: changed.append(args) or (True, True)

        result = service_entry.Service.watchlist_add_key(
            service_proxy(service_entry),
            "rating_key=%s&plex_type=video" % self.MOVIE_KEY,
        )

        self.assertFalse(result)
        self.assertEqual(changed, [])
        self.assertEqual(notifications, [])
        self.assertEqual(xbmc.commands, [])

    def test_the_odyssey_exact_tmdb_identity_uses_its_canonical_plex_key(self):
        service_entry, xbmc, notifications, _ = load_service_entry()
        downloader = DownloadRecorder(
            [
                FakeResponse(
                    {
                        "MediaContainer": {
                            "Metadata": {
                                "type": "movie",
                                "guid": "plex://movie/%s" % self.THE_ODYSSEY_KEY,
                                "ratingKey": self.THE_ODYSSEY_KEY,
                            }
                        }
                    }
                ),
                FakeResponse(
                    self._watchlist_state_payload(self.THE_ODYSSEY_KEY, False)
                ),
                FakeResponse(),
                FakeResponse(
                    self._watchlist_state_payload(self.THE_ODYSSEY_KEY, True)
                ),
            ]
        )
        service_entry.watchlist.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_tmdb(
            service_proxy(service_entry),
            "tmdb_id=%s&tmdb_type=movie" % self.THE_ODYSSEY_ID,
        )

        self.assertTrue(result)
        self.assertEqual(notifications, [])
        self.assertEqual(xbmc.commands, ["Container.Refresh"])
        self.assertEqual(
            downloader.calls[0][2]["parameters"],
            {"guid": "tmdb://%s" % self.THE_ODYSSEY_ID, "type": 1},
        )
        self.assertEqual(
            downloader.calls[2][2]["parameters"],
            {"ratingKey": self.THE_ODYSSEY_KEY},
        )

    def test_invalid_tmdb_identity_fails_before_any_network_action(self):
        service_entry, xbmc, notifications, _ = load_service_entry()
        downloader = DownloadRecorder([])
        service_entry.watchlist.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_tmdb(
            service_proxy(service_entry), "tmdb_id=not-a-number&tmdb_type=movie"
        )

        self.assertFalse(result)
        self.assertEqual(downloader.calls, [])
        self.assertEqual(xbmc.commands, [])
        self.assertEqual(
            notifications[0][0][2],
            "Plex could not uniquely match this TMDb item for Watchlist.",
        )

    def test_status_response_warms_the_item_cache_after_the_detail_changes(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.MOVIE_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity

        def state_after_detail_closes(rating_key):
            self.assertEqual(rating_key, self.MOVIE_KEY)
            properties[watchlist.DETAIL_IDENTITY] = "plex.next-item"
            return True

        watchlist.state = state_after_detail_closes

        self.assertFalse(
            watchlist.status_key(
                {"rating_key": self.MOVIE_KEY, "plex_type": "movie"}
            )
        )
        self.assertEqual(
            properties[watchlist._item_state_property(identity)], "present"
        )

    def test_provider_user_state_hint_bootstraps_a_direct_detail_without_network(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.MOVIE_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity

        self.assertTrue(
            watchlist.bootstrap_status_key(
                {
                    "rating_key": self.MOVIE_KEY,
                    "plex_type": "movie",
                    "watchlist_hint": "1",
                }
            )
        )
        self.assertEqual(
            properties[watchlist._item_state_property(identity)], "present"
        )
        self.assertEqual(watchlist.provider_state_hint("0"), "absent")
        self.assertEqual(watchlist.provider_state_hint(0), "absent")
        self.assertIsNone(watchlist.provider_state_hint("unrecognized"))

    def test_provider_user_state_hint_cannot_replace_a_pending_mutation(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.MOVIE_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity
        watchlist.begin_key(
            {"rating_key": self.MOVIE_KEY, "plex_type": "movie"}, "present"
        )

        self.assertFalse(
            watchlist.seed_key_status_hint(
                {
                    "rating_key": self.MOVIE_KEY,
                    "plex_type": "movie",
                    "watchlist_hint": "0",
                }
            )
        )
        self.assertNotIn(watchlist._item_state_property(identity), properties)

    def test_status_response_cannot_overwrite_a_mutation_that_started_during_io(self):
        service_entry, _, _, properties = load_service_entry()
        watchlist = service_entry.watchlist
        identity = "plex.%s" % self.MOVIE_KEY
        properties[watchlist.DETAIL_IDENTITY] = identity

        def stale_state(rating_key):
            self.assertEqual(rating_key, self.MOVIE_KEY)
            watchlist.begin_key(
                {"rating_key": self.MOVIE_KEY, "plex_type": "movie"}, "present"
            )
            return False

        watchlist.state = stale_state

        self.assertFalse(
            watchlist.status_key(
                {"rating_key": self.MOVIE_KEY, "plex_type": "movie"}
            )
        )
        self.assertNotIn(watchlist._item_state_property(identity), properties)

    def test_verified_watchlist_change_invalidates_discovery_rankings(self):
        service_entry, _, _, _ = load_service_entry()
        watchlist = service_entry.watchlist
        invalidations = []
        states = iter((False, True))
        watchlist.state = lambda rating_key: next(states)
        watchlist._action = lambda api_type, rating_key: True
        watchlist.discover_cache.invalidate = lambda: invalidations.append(True)

        self.assertEqual(
            watchlist.change("addToWatchlist", self.MOVIE_KEY), (True, True)
        )
        self.assertEqual(invalidations, [True])


if __name__ == "__main__":
    unittest.main()
