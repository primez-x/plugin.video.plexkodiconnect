import importlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_entrypoint():
    module_names = (
        "xbmc",
        "xbmcgui",
        "xbmcplugin",
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
    resources_lib = sys.modules.get("resources.lib")
    if resources_lib is not None:
        for attribute in (
            "entrypoint",
            "utils",
            "clientinfo",
            "path_ops",
            "downloadutils",
            "plex_api",
            "plex_functions",
            "variables",
            "app",
            "widgets",
            "library_sync",
        ):
            if hasattr(resources_lib, attribute):
                delattr(resources_lib, attribute)
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType("xbmc")
    xbmc.sleep = lambda milliseconds: None
    sys.modules["xbmc"] = xbmc

    dialog_calls = []
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.ListItem = object

    class Dialog(object):
        def info(self, listitem):
            dialog_calls.append(listitem)

    xbmcgui.Dialog = Dialog
    sys.modules["xbmcgui"] = xbmcgui

    directory_calls = []
    xbmcplugin = types.ModuleType("xbmcplugin")
    xbmcplugin.SORT_METHOD_UNSORTED = 0
    xbmcplugin.setContent = lambda *args, **kwargs: None
    xbmcplugin.addDirectoryItems = lambda *args, **kwargs: directory_calls.append(args)
    xbmcplugin.addSortMethod = lambda *args, **kwargs: None
    sys.modules["xbmcplugin"] = xbmcplugin

    modules = {}
    for module_name in module_names[4:]:
        modules[module_name] = types.ModuleType(module_name)
        sys.modules[module_name] = modules[module_name]

    utils = modules["resources.lib.utils"]
    utils.window = lambda key: {
        "plex_authenticated": "true",
        "plex_token": "token",
        "plex_restricteduser": "",
    }.get(key, "")
    utils.settings = lambda key: "0"
    utils.extend_url = lambda url, params: "%s?%s" % (url, urlencode(params))

    modules["resources.lib.clientinfo"].getXArgsDeviceInfo = (
        lambda options=None, include_token=True: dict(options or {})
    )
    modules["resources.lib.downloadutils"].DownloadUtils = object
    modules["resources.lib.app"].init = lambda entrypoint=False: None

    variables = modules["resources.lib.variables"]
    variables.ADDON_ID = "plugin.video.plexkodiconnect"
    variables.CONTENT_TYPE_FILE = "files"
    variables.CONTENT_FROM_PLEX_TYPE = {"movie": "movies", "show": "tvshows"}

    class API(object):
        def __init__(self, xml):
            self.xml = xml
            self.plex_type = xml.get("type")

    mass_api_calls = []

    def mass_api(xml, check_by_guid=False):
        mass_api_calls.append(list(xml))
        return [API(child) for child in xml]

    modules["resources.lib.plex_api"].API = API
    modules["resources.lib.plex_api"].mass_api = mass_api
    modules["resources.lib.library_sync.nodes"].NODE_TYPES = {}

    widgets = modules["resources.lib.widgets"]
    widgets.generate_item = lambda api: {
        "title": api.xml.get("title"),
        "label": api.xml.get("title"),
        "type": api.plex_type,
        "file": "plugin://plugin.video.plexkodiconnect?mode=play",
        "extraproperties": {},
    }
    widgets.prepare_listitem = lambda item: item
    widgets.create_listitem = lambda item, as_tuple=True, **kwargs: (
        (item["file"], item, item.get("isFolder", False)) if as_tuple else item
    )

    entrypoint = importlib.import_module("resources.lib.entrypoint")
    entrypoint.sys.argv = ["plugin://plugin.video.plexkodiconnect", "1", ""]
    return entrypoint, directory_calls, dialog_calls, mass_api_calls


def provider_metadata(rating_key="abc123"):
    root = ET.Element("MediaContainer")
    ET.SubElement(
        root,
        "Video",
        type="movie",
        title="Provider Movie",
        ratingKey=rating_key,
        guid="plex://movie/%s" % rating_key,
    )
    return root


class DiscoverListingTests(unittest.TestCase):
    def test_provider_redirect_uses_one_bounded_batch_for_widget_latency(self):
        entrypoint, _, _, _ = load_entrypoint()
        services = ["service-%s" % index for index in range(41)]
        location = (
            "https://discover.provider.plex.tv/hubs/sections/home/new-for-you?%s"
            % (
                urlencode(
                    [
                        ("x-plex-preferred-services[]", service)
                        for service in services
                    ]
                    + [("other", "value")]
                )
            )
        )

        class Response(object):
            def __init__(self, status_code, content=b"", headers=None):
                self.status_code = status_code
                self.content = content
                self.headers = headers or {}

        class Downloader(object):
            def __init__(self):
                self.calls = []
                self.responses = [
                    Response(302, headers={"Location": location}),
                    Response(200, ET.tostring(provider_metadata())),
                ]

            def downloadUrl(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return self.responses.pop(0)

        downloader = Downloader()
        entrypoint.DU = lambda: downloader

        xmls = entrypoint._provider_xmls(
            "https://discover.provider.plex.tv/hubs/sections/home/new-for-you",
            ("discover.provider.plex.tv",),
        )

        self.assertEqual(len(xmls), 1)
        self.assertEqual(len(downloader.calls), 2)
        redirected_url, kwargs = downloader.calls[1]
        self.assertEqual(
            redirected_url.count("x-plex-preferred-services%5B%5D="), 20
        )
        self.assertFalse(kwargs["allow_redirects"])

    def test_hub_items_use_bulk_database_lookup_and_direct_detail_routes(self):
        entrypoint, directory_calls, _, mass_api_calls = load_entrypoint()
        xml = provider_metadata()
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]

        entrypoint.discover_hub("new-for-you")

        self.assertEqual(mass_api_calls, [[xml[0]]])
        self.assertEqual(len(directory_calls), 1)
        _, items, _ = directory_calls[0]
        path, item, is_folder = items[0]
        self.assertIn("mode=discover_detail", path)
        self.assertIn("rating_key=abc123", path)
        self.assertFalse(is_folder)
        self.assertEqual(item["IsPlayable"], "false")
        self.assertEqual(item["extraproperties"]["ratingKey"], "abc123")
        self.assertEqual(item["extraproperties"]["PlexDiscoverDetail"], "true")

    def test_detail_uses_exact_provider_key_and_opens_native_info(self):
        entrypoint, _, dialog_calls, mass_api_calls = load_entrypoint()
        xml = provider_metadata()
        entrypoint._provider_xmls = lambda *args, **kwargs: [xml]

        self.assertTrue(entrypoint.discover_detail("abc123"))

        self.assertEqual(mass_api_calls, [[xml[0]]])
        self.assertEqual(len(dialog_calls), 1)
        item = dialog_calls[0]
        self.assertEqual(item["file"], "")
        self.assertEqual(item["IsPlayable"], "false")
        self.assertEqual(item["extraproperties"]["ratingKey"], "abc123")
        self.assertEqual(
            item["extraproperties"]["plexguid"], "plex://movie/abc123"
        )


if __name__ == "__main__":
    unittest.main()
