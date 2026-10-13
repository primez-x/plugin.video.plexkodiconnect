import runpy
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeWindow(object):
    def __init__(self):
        self.properties = {}

    def getProperty(self, key):
        return self.properties.get(key, "")

    def setProperty(self, key, value):
        self.properties[key] = value

    def clearProperty(self, key):
        self.properties.pop(key, None)


class FakeMonitor(object):
    def waitForAbort(self, timeout=0):
        return False

    def abortRequested(self):
        return False


def run_context(filename, labels, window_properties=None):
    for module_name in ("xbmc", "xbmcgui", "resources.lib.transfer"):
        sys.modules.pop(module_name, None)
    # `from resources.lib import transfer` prefers the package attribute
    # over sys.modules, so drop a stale one bound to another test's stubs
    package = sys.modules.get("resources.lib")
    if package is not None:
        package.__dict__.pop("transfer", None)

    def get_info_label(label):
        if label == "System.BuildVersion":
            return "21.3"
        return labels.get(label, "")

    xbmc = types.ModuleType("xbmc")
    xbmc.getInfoLabel = get_info_label
    xbmc.sleep = lambda milliseconds: None
    xbmc.Monitor = FakeMonitor
    xbmc.executeJSONRPC = lambda request: '{"result": "OK"}'
    sys.modules["xbmc"] = xbmc

    window = FakeWindow()
    window.properties.update(window_properties or {})
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.Window = lambda window_id: window
    sys.modules["xbmcgui"] = xbmcgui

    runpy.run_path(str(REPO_ROOT / filename), run_name="__main__")
    return window


class WatchlistContextTmdbTests(unittest.TestCase):
    def test_add_context_prefers_tmdb_unique_id(self):
        window = run_context(
            "context_watchlist_add_search.py",
            {
                "ListItem.UniqueID(tmdb)": "603",
                "ListItem.Property(tmdb_id)": "999",
                "ListItem.DBTYPE": "movie",
            },
        )

        self.assertEqual(
            window.properties["plexkodiconnect.command"],
            "WATCHLIST_ADD_TMDB?tmdb_id=603&tmdb_type=movie",
        )

    def test_remove_context_falls_back_to_tmdb_property(self):
        window = run_context(
            "context_watchlist_remove_search.py",
            {
                "ListItem.UniqueID(tmdb)": "",
                "ListItem.Property(tmdb_id)": "132159",
                "ListItem.DBTYPE": "tvshow",
            },
        )

        self.assertEqual(
            window.properties["plexkodiconnect.command"],
            "WATCHLIST_REMOVE_TMDB?tmdb_id=132159&tmdb_type=tvshow",
        )

    def test_add_context_falls_back_to_tmdb_helper_monitor_identity(self):
        window = run_context(
            "context_watchlist_add_search.py",
            {
                "ListItem.UniqueID(tmdb)": "",
                "ListItem.Property(tmdb_id)": "",
                "ListItem.DBTYPE": "",
            },
            {
                "TMDbHelper.ListItem.Monitor.TMDb_ID": "12345",
                "TMDbHelper.ListItem.Monitor.TMDb_Type": "tv",
            },
        )

        self.assertEqual(
            window.properties["plexkodiconnect.command"],
            "WATCHLIST_ADD_TMDB?tmdb_id=12345&tmdb_type=tv",
        )


if __name__ == "__main__":
    unittest.main()
