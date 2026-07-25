import importlib
import sys
import types
import unittest
from pathlib import Path
from urllib.parse import parse_qsl


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

    xbmc = types.ModuleType("xbmc")
    xbmc.commands = []
    xbmc.executebuiltin = xbmc.commands.append
    sys.modules["xbmc"] = xbmc
    sys.modules["xbmcvfs"] = types.ModuleType("xbmcvfs")

    modules = {}
    for module_name in module_names[4:]:
        modules[module_name] = types.ModuleType(module_name)
        sys.modules[module_name] = modules[module_name]

    notifications = []
    utils = modules["resources.lib.utils"]
    utils.parse_qsl = parse_qsl
    utils.window = lambda key, value=None: "plex-token" if key == "plex_token" else ""
    utils.lang = lambda string_id: "PlexKodiConnect"
    utils.dialog = lambda *args, **kwargs: notifications.append((args, kwargs))

    def get_headers(options=None, include_token=True):
        headers = {"Accept": "*/*", "X-Plex-Product": "PlexKodiConnect"}
        headers.update(options or {})
        return headers

    modules["resources.lib.clientinfo"].getXArgsDeviceInfo = get_headers
    modules["resources.lib.loghandler"].config = lambda: None
    modules["resources.lib.windows"].userselect = modules[
        "resources.lib.windows.userselect"
    ]

    service_entry = importlib.import_module("resources.lib.service_entry")
    return service_entry, xbmc, notifications


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

    def test_tmdb_watchlist_action_resolves_exact_id_then_mutates(self):
        service_entry, xbmc, notifications = load_service_entry()
        downloader = DownloadRecorder(
            [FakeResponse(self._metadata_payload()), FakeResponse()]
        )
        service_entry.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_tmdb(
            service_proxy(service_entry), "tmdb_id=603&tmdb_type=movie"
        )

        self.assertTrue(result)
        self.assertEqual(notifications, [])
        self.assertEqual(xbmc.commands, ["Container.Refresh"])
        self.assertEqual(len(downloader.calls), 2)

        metadata_url, _, metadata_kwargs = downloader.calls[0]
        self.assertEqual(metadata_url, service_entry.plex_discover.METADATA_MATCH_URL)
        self.assertEqual(
            metadata_kwargs["parameters"], {"guid": "tmdb://603", "type": 1}
        )
        self.assertEqual(metadata_kwargs["headerOptions"]["Accept"], "application/json")
        self.assertEqual(
            metadata_kwargs["headerOptions"]["X-Plex-Product"], "PlexKodiConnect"
        )
        self.assertTrue(metadata_kwargs["return_response"])
        self.assertFalse(metadata_kwargs["authenticate"])

        action_url, _, action_kwargs = downloader.calls[1]
        self.assertEqual(
            action_url,
            "https://discover.provider.plex.tv/actions/addToWatchlist",
        )
        self.assertEqual(action_kwargs["action_type"], "PUT")
        self.assertEqual(action_kwargs["parameters"], {"ratingKey": self.MOVIE_KEY})
        self.assertTrue(action_kwargs["return_response"])
        self.assertFalse(action_kwargs["authenticate"])

    def test_failed_action_does_not_refresh_or_claim_success(self):
        service_entry, xbmc, notifications = load_service_entry()
        downloader = DownloadRecorder(
            [
                FakeResponse(self._metadata_payload()),
                FakeResponse(ok=False, status_code=500),
            ]
        )
        service_entry.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_tmdb(
            service_proxy(service_entry), "tmdb_id=603&tmdb_type=movie"
        )

        self.assertFalse(result)
        self.assertEqual(xbmc.commands, [])
        self.assertEqual(len(downloader.calls), 2)
        self.assertEqual(notifications[0][0][2], "Plex Watchlist could not be updated.")

    def test_direct_discovery_rating_key_uses_the_same_checked_action(self):
        service_entry, xbmc, notifications = load_service_entry()
        downloader = DownloadRecorder([FakeResponse()])
        service_entry.downloadutils.DownloadUtils = lambda: downloader

        result = service_entry.Service.watchlist_add_key(
            service_proxy(service_entry),
            "rating_key=%s&plex_type=movie" % self.MOVIE_KEY,
        )

        self.assertTrue(result)
        self.assertEqual(notifications, [])
        self.assertEqual(xbmc.commands, ["Container.Refresh"])
        self.assertEqual(len(downloader.calls), 1)
        self.assertEqual(
            downloader.calls[0][2]["parameters"], {"ratingKey": self.MOVIE_KEY}
        )

    def test_invalid_tmdb_identity_fails_before_any_network_action(self):
        service_entry, xbmc, notifications = load_service_entry()
        downloader = DownloadRecorder([])
        service_entry.downloadutils.DownloadUtils = lambda: downloader

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


if __name__ == "__main__":
    unittest.main()
