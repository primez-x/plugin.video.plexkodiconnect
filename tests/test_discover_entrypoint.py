import importlib
import sys
import types
import unittest
from pathlib import Path
from urllib.parse import urlencode


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class FakeResponse(object):
    def __init__(self, payload=None, ok=True, status_code=200):
        self.payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeDownloader(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def downloadUrl(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


class FakeListItem(object):
    def __init__(self, label='', path=None, **kwargs):
        self.label = label
        self.path = path
        self.info = None
        self.art = {}
        self.properties = {}
        self.context_items = []
        self.label2 = ''

    def setInfo(self, media_type, info):
        self.info = (media_type, info)

    def setArt(self, art):
        self.art.update(art)

    def setProperty(self, key, value):
        self.properties[key] = value

    def setLabel2(self, label):
        self.label2 = label

    def addContextMenuItems(self, items):
        self.context_items.extend(items)


def load_entrypoint(responses):
    module_names = (
        'xbmc',
        'xbmcgui',
        'xbmcplugin',
        'resources.lib.entrypoint',
        'resources.lib.utils',
        'resources.lib.path_ops',
        'resources.lib.downloadutils',
        'resources.lib.plex_api',
        'resources.lib.plex_functions',
        'resources.lib.variables',
        'resources.lib.app',
        'resources.lib.widgets',
        'resources.lib.library_sync',
        'resources.lib.library_sync.nodes',
    )
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType('xbmc')
    xbmc.sleep = lambda *args, **kwargs: None
    sys.modules['xbmc'] = xbmc

    xbmcgui = types.ModuleType('xbmcgui')
    xbmcgui.ListItem = FakeListItem
    sys.modules['xbmcgui'] = xbmcgui

    directory_items = []
    xbmcplugin = types.ModuleType('xbmcplugin')
    xbmcplugin.SORT_METHOD_UNSORTED = 0
    xbmcplugin.addDirectoryItem = lambda **kwargs: directory_items.append(kwargs)
    xbmcplugin.setContent = lambda *args, **kwargs: None
    xbmcplugin.addSortMethod = lambda *args, **kwargs: None
    sys.modules['xbmcplugin'] = xbmcplugin

    window_values = {
        'plex_authenticated': 'true',
        'plex_token': 'test-plex-token',
        'plex_restricteduser': 'false'
    }
    utils = types.ModuleType('resources.lib.utils')
    utils.window = window_values.get
    utils.settings = lambda *args, **kwargs: ''
    utils.lang = lambda identifier: 'PlexKodiConnect' if identifier == 29999 \
        else 'Add to Plex Watchlist'
    utils.extend_url = lambda url, params: '%s%s%s' % (
        url,
        '&' if '?' in url else '?',
        urlencode(params))
    utils.dialog_calls = []
    utils.dialog = lambda *args, **kwargs: utils.dialog_calls.append((args, kwargs))
    sys.modules['resources.lib.utils'] = utils

    sys.modules['resources.lib.path_ops'] = types.ModuleType('resources.lib.path_ops')

    downloader = FakeDownloader(responses)
    downloadutils = types.ModuleType('resources.lib.downloadutils')
    downloadutils.DownloadUtils = lambda: downloader
    sys.modules['resources.lib.downloadutils'] = downloadutils

    plex_api = types.ModuleType('resources.lib.plex_api')
    plex_api.API = object
    plex_api.mass_api = lambda *args, **kwargs: []
    sys.modules['resources.lib.plex_api'] = plex_api
    sys.modules['resources.lib.plex_functions'] = types.ModuleType(
        'resources.lib.plex_functions')

    variables = types.ModuleType('resources.lib.variables')
    variables.ADDON_ID = 'plugin.video.plexkodiconnect'
    variables.CONTENT_TYPE_VIDEO = 'videos'
    sys.modules['resources.lib.variables'] = variables

    app = types.ModuleType('resources.lib.app')
    app.init_calls = []
    app.init = lambda **kwargs: app.init_calls.append(kwargs)
    sys.modules['resources.lib.app'] = app
    sys.modules['resources.lib.widgets'] = types.ModuleType('resources.lib.widgets')

    library_sync = types.ModuleType('resources.lib.library_sync')
    library_sync.__path__ = []
    nodes = types.ModuleType('resources.lib.library_sync.nodes')
    nodes.NODE_TYPES = {}
    library_sync.nodes = nodes
    sys.modules['resources.lib.library_sync'] = library_sync
    sys.modules['resources.lib.library_sync.nodes'] = nodes

    entrypoint = importlib.import_module('resources.lib.entrypoint')
    entrypoint.sys.argv = ['plugin://plugin.video.plexkodiconnect', '1', '']
    return entrypoint, downloader, directory_items, utils


class DiscoverEntrypointTests(unittest.TestCase):
    def tearDown(self):
        for module_name in (
            'xbmc',
            'xbmcgui',
            'xbmcplugin',
            'resources.lib.entrypoint',
            'resources.lib.utils',
            'resources.lib.path_ops',
            'resources.lib.downloadutils',
            'resources.lib.plex_api',
            'resources.lib.plex_functions',
            'resources.lib.variables',
            'resources.lib.app',
            'resources.lib.widgets',
            'resources.lib.library_sync',
            'resources.lib.library_sync.nodes',
        ):
            sys.modules.pop(module_name, None)

    def test_tmdb_watchlist_add_uses_exact_resolver_then_put_action(self):
        entrypoint, downloader, _, utils = load_entrypoint([
            FakeResponse({
                'MediaContainer': {
                    'Metadata': [
                        {'guid': 'plex://movie/the-matrix', 'type': 'movie'}
                    ]
                }
            }),
            FakeResponse()
        ])

        success = entrypoint.add_tmdb_to_watchlist('603', 'movie')

        self.assertTrue(success)
        self.assertEqual(len(downloader.calls), 2)
        resolver_args, resolver_kwargs = downloader.calls[0]
        self.assertEqual(resolver_args[0], entrypoint.plex_discover.METADATA_MATCH_URL)
        self.assertEqual(resolver_kwargs['parameters'], {
            'guid': 'tmdb://603',
            'type': 1
        })
        self.assertEqual(resolver_kwargs['action_type'], 'GET')
        self.assertFalse(resolver_kwargs['authenticate'])
        self.assertTrue(resolver_kwargs['return_response'])
        self.assertEqual(resolver_kwargs['headerOptions'], {
            'X-Plex-Token': 'test-plex-token',
            'Accept': 'application/json'
        })

        action_args, action_kwargs = downloader.calls[1]
        self.assertEqual(action_args[0], entrypoint.plex_discover.WATCHLIST_ACTION_URL)
        self.assertEqual(action_kwargs['parameters'], {'ratingKey': 'the-matrix'})
        self.assertEqual(action_kwargs['action_type'], 'PUT')
        self.assertEqual(len(utils.dialog_calls), 1)
        self.assertEqual(utils.dialog_calls[0][0][1], 'PlexKodiConnect')

    def test_failed_action_does_not_report_success_or_retry_with_title(self):
        entrypoint, downloader, _, utils = load_entrypoint([
            FakeResponse({
                'MediaContainer': {
                    'Metadata': [
                        {'guid': 'plex://movie/the-matrix', 'type': 'movie'}
                    ]
                }
            }),
            FakeResponse(ok=False, status_code=400)
        ])

        success = entrypoint.add_tmdb_to_watchlist('603', 'movie')

        self.assertFalse(success)
        self.assertEqual(len(downloader.calls), 2)
        self.assertNotIn('title', downloader.calls[1][1]['parameters'])
        self.assertEqual(utils.dialog_calls[0][0][2],
                         'Plex Watchlist could not be updated.')

    def test_discover_search_renders_nonplayable_external_cards_with_context_action(self):
        entrypoint, downloader, directory_items, _ = load_entrypoint([
            FakeResponse({
                'MediaContainer': {
                    'SearchResults': [{
                        'id': 'external',
                        'SearchResult': [{
                            'Metadata': {
                                'guid': 'plex://movie/the-matrix',
                                'type': 'movie',
                                'title': 'The Matrix',
                                'thumb': 'https://example.test/poster.jpg',
                                'summary': 'A test result',
                                'year': 1999
                            }
                        }]
                    }]
                }
            })
        ])

        entrypoint.discover_search('matrix')

        self.assertEqual(len(downloader.calls), 1)
        self.assertEqual(downloader.calls[0][1]['parameters'], {
            'query': 'matrix',
            'limit': 100,
            'searchTypes': 'movies,tv',
            'searchProviders': 'discover',
            'includeMetadata': 1
        })
        self.assertEqual(len(directory_items), 1)
        item = directory_items[0]['listitem']
        self.assertEqual(item.properties['IsPlayable'], 'false')
        self.assertEqual(item.properties['Plex.Discover.Guid'],
                         'plex://movie/the-matrix')
        self.assertEqual(item.art['poster'], 'https://example.test/poster.jpg')
        self.assertIn('RunPlugin(', item.context_items[0][1])
        self.assertIn('guid=plex%3A%2F%2Fmovie%2Fthe-matrix', item.context_items[0][1])


if __name__ == '__main__':
    unittest.main()
