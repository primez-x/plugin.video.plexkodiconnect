import importlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class DownloadRecorder(object):
    def __init__(self, pages):
        self.calls = []
        self.pages = list(pages)

    def downloadUrl(self, url, *args, **kwargs):
        self.calls.append((url, args, kwargs))
        return self.pages.pop(0)


def watchlist_page(total_size, count):
    page = ET.Element("MediaContainer", totalSize=str(total_size))
    for index in range(count):
        ET.SubElement(page, "Video", ratingKey=str(index))
    return page


def load_entrypoint():
    module_names = (
        "xbmc",
        "xbmcplugin",
        "xbmcgui",
        "resources.lib.entrypoint",
        "resources.lib.utils",
        "resources.lib.clientinfo",
        "resources.lib.path_ops",
        "resources.lib.downloadutils",
        "resources.lib.plex_api",
        "resources.lib.plex_functions",
        "resources.lib.variables",
        "resources.lib.app",
        "resources.lib.widgets",
        "resources.lib.library_sync",
        "resources.lib.library_sync.nodes",
    )
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType("xbmc")
    sys.modules["xbmc"] = xbmc
    sys.modules["xbmcplugin"] = types.ModuleType("xbmcplugin")
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.ListItem = object
    sys.modules["xbmcgui"] = xbmcgui

    modules = {}
    for module_name in module_names[4:]:
        modules[module_name] = types.ModuleType(module_name)
        sys.modules[module_name] = modules[module_name]

    modules["resources.lib.utils"].window = lambda key: "plex-token"
    modules["resources.lib.clientinfo"].getXArgsDeviceInfo = (
        lambda options=None, include_token=True: dict(options or {})
    )
    modules["resources.lib.downloadutils"].DownloadUtils = object
    modules["resources.lib.plex_api"].API = object
    modules["resources.lib.plex_api"].mass_api = lambda *args, **kwargs: []
    modules["resources.lib.library_sync.nodes"].NODE_TYPES = {}
    return importlib.import_module("resources.lib.entrypoint")


class WatchlistListingTests(unittest.TestCase):
    def test_downloads_and_merges_every_provider_page_at_its_100_item_limit(self):
        entrypoint = load_entrypoint()
        downloader = DownloadRecorder([watchlist_page(101, 100), watchlist_page(101, 1)])
        entrypoint.DU = lambda: downloader

        result = entrypoint._download_watchlist()

        self.assertEqual(len(result), 101)
        self.assertEqual(
            [call[2]["parameters"] for call in downloader.calls],
            [
                {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 100},
                {"X-Plex-Container-Start": 100, "X-Plex-Container-Size": 100},
            ],
        )


if __name__ == "__main__":
    unittest.main()
