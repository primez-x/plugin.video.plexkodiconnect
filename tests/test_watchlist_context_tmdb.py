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


def run_context(filename, labels, window_properties=None):
    for module_name in ("xbmc", "xbmcgui"):
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType("xbmc")
    xbmc.getInfoLabel = labels.get
    xbmc.sleep = lambda milliseconds: None
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
