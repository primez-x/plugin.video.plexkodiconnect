import importlib
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class ArtworkUrlTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop('resources.lib.artwork_urls', None)
        self.artwork_urls = importlib.import_module('resources.lib.artwork_urls')

    def test_normalizes_plex_photo_transcode_url_to_current_server(self):
        old_url = (
            'https://75-163-143-71.8040ad440812489a81c0accac657c560.plex.direct:32400'
            '/photo/:/transcode?width=1920&height=1920&minSize=1&upscale=0'
            '&url=/library/metadata/4488/thumb/1779675951&X-Plex-Token=token'
        )
        server = (
            'https://192-168-0-10.8040ad440812489a81c0accac657c560.plex.direct:32400'
        )

        normalized = self.artwork_urls.normalize_plex_artwork_url(old_url, server=server)

        self.assertEqual(
            normalized,
            server
            + '/photo/:/transcode?width=1920&height=1920&minSize=1&upscale=0'
            + '&url=/library/metadata/4488/thumb/1779675951&X-Plex-Token=token',
        )

    def test_leaves_external_artwork_url_unchanged(self):
        url = 'https://assets.fanart.tv/fanart/archer-2009-51484c5a2bcdf.jpg'

        normalized = self.artwork_urls.normalize_plex_artwork_url(
            url,
            server='https://192-168-0-10.example.plex.direct:32400',
        )

        self.assertEqual(normalized, url)

    def test_leaves_non_artwork_plex_url_unchanged(self):
        url = (
            'https://75-163-143-71.8040ad440812489a81c0accac657c560.plex.direct:32400'
            '/library/parts/23630/1779159316/file.mkv?X-Plex-Token=token'
        )

        normalized = self.artwork_urls.normalize_plex_artwork_url(
            url,
            server='https://192-168-0-10.example.plex.direct:32400',
        )

        self.assertEqual(normalized, url)

    def test_widget_prepare_listitem_normalizes_thumbnail_override(self):
        for module_name in (
            'xbmc',
            'xbmcgui',
            'xbmcvfs',
            'resources.lib.app',
            'resources.lib.artwork_urls',
            'resources.lib.json_rpc',
            'resources.lib.utils',
            'resources.lib.variables',
            'resources.lib.widgets',
        ):
            sys.modules.pop(module_name, None)

        xbmc = types.ModuleType('xbmc')
        xbmc.getCacheThumbName = lambda image: 'unused.tbn'
        sys.modules['xbmc'] = xbmc
        class FakeListItem(object):
            last = None

            def __init__(self, *args, **kwargs):
                self.art = {}
                self.properties = {}
                FakeListItem.last = self

            def setArt(self, art):
                self.art.update(art)

            def setInfo(self, *args, **kwargs):
                pass

            def setProperty(self, key, value):
                self.properties[key] = value

            def addStreamInfo(self, *args, **kwargs):
                pass

            def addContextMenuItems(self, *args, **kwargs):
                pass

        xbmcgui = types.ModuleType('xbmcgui')
        xbmcgui.ListItem = FakeListItem
        sys.modules['xbmcgui'] = xbmcgui
        xbmcvfs = types.ModuleType('xbmcvfs')
        xbmcvfs.exists = lambda path: True
        xbmcvfs.copy = lambda source, target: True
        sys.modules['xbmcvfs'] = xbmcvfs

        app = types.ModuleType('resources.lib.app')
        app.CONN = SimpleNamespace(
            server='https://192-168-0-10.8040ad440812489a81c0accac657c560.plex.direct:32400'
        )
        sys.modules['resources.lib.app'] = app
        sys.modules['resources.lib.json_rpc'] = types.ModuleType('resources.lib.json_rpc')

        utils = types.ModuleType('resources.lib.utils')
        utils.unquote = unquote
        sys.modules['resources.lib.utils'] = utils

        variables = types.ModuleType('resources.lib.variables')
        variables.KODIVERSION = 19
        sys.modules['resources.lib.variables'] = variables

        widgets = importlib.import_module('resources.lib.widgets')
        old_url = (
            'https://75-163-143-71.8040ad440812489a81c0accac657c560.plex.direct:32400'
            '/photo/:/transcode?width=1920&height=1920&minSize=1&upscale=0'
            '&url=/library/metadata/4488/thumb/1779675951&X-Plex-Token=token'
        )
        encoded_old_url = 'image://%s/' % quote(old_url, safe='')
        item = {
            'type': 'episode',
            'title': 'Blood Test',
            'label': 'Blood Test',
            'file': 'plugin://plugin.video.plexkodiconnect.tvshows/4457/?mode=play',
            'thumbnail': encoded_old_url,
            'art': {'thumb': encoded_old_url},
            'season': 2,
            'episode': 3,
            'artist': [],
            'streamdetails': {'video': [], 'audio': [], 'subtitle': []},
        }

        prepared = widgets.prepare_listitem(item)

        self.assertIn('192-168-0-10', prepared['thumbnail'])
        self.assertIn('192-168-0-10', prepared['art']['thumb'])
        self.assertNotIn('75-163-143-71', prepared['thumbnail'])
        self.assertNotIn('75-163-143-71', prepared['art']['thumb'])

    def test_widget_create_listitem_normalizes_final_thumbnail_art(self):
        self.test_widget_prepare_listitem_normalizes_thumbnail_override()
        widgets = sys.modules['resources.lib.widgets']
        fake_listitem = sys.modules['xbmcgui'].ListItem
        old_url = (
            'https://75-163-143-71.8040ad440812489a81c0accac657c560.plex.direct:32400'
            '/photo/:/transcode?width=1920&height=1920&minSize=1&upscale=0'
            '&url=/library/metadata/4488/thumb/1779675951&X-Plex-Token=token'
        )
        item = {
            'type': 'episode',
            'title': 'Blood Test',
            'label': 'Blood Test',
            'file': 'plugin://plugin.video.plexkodiconnect.tvshows/4457/?mode=play',
            'thumbnail': old_url,
            'art': {
                'thumb': (
                    'https://192-168-0-10.8040ad440812489a81c0accac657c560.plex.direct:32400'
                    '/photo/:/transcode?width=1920&height=1920&minSize=1&upscale=0'
                    '&url=/library/metadata/4488/thumb/1779675951&X-Plex-Token=token'
                )
            },
            'extraproperties': {},
            'season': 2,
            'episode': 3,
            'artist': [],
            'streamdetails': {'video': {}, 'audio': {}, 'subtitle': {}},
        }

        widgets.create_listitem(item, listitem=fake_listitem)

        self.assertIn('192-168-0-10', fake_listitem.last.art['thumb'])
        self.assertNotIn('75-163-143-71', fake_listitem.last.art['thumb'])

    def test_normalizes_existing_artwork_table_rows(self):
        artwork_urls = self.artwork_urls
        server = (
            'https://192-168-0-10.8040ad440812489a81c0accac657c560.plex.direct:32400'
        )
        old_art = (
            'https://75-163-143-71.8040ad440812489a81c0accac657c560.plex.direct:32400'
            '/photo/:/transcode?width=1920&height=1920&minSize=1&upscale=0'
            '&url=/library/metadata/4488/thumb/1779675951&X-Plex-Token=token'
        )
        fanart = 'https://assets.fanart.tv/fanart/archer-2009-51484c5a2bcdf.jpg'
        media = (
            'https://75-163-143-71.8040ad440812489a81c0accac657c560.plex.direct:32400'
            '/library/parts/23630/1779159316/file.mkv?X-Plex-Token=token'
        )
        conn = sqlite3.connect(':memory:')
        try:
            cursor = conn.cursor()
            cursor.execute('CREATE TABLE art(art_id INTEGER PRIMARY KEY, url TEXT)')
            cursor.executemany(
                'INSERT INTO art(art_id, url) VALUES (?, ?)',
                ((1, old_art), (2, fanart), (3, media)),
            )

            changed = artwork_urls.normalize_artwork_table_urls(cursor, server=server)

            self.assertEqual(changed, 1)
            rows = dict(cursor.execute('SELECT art_id, url FROM art'))
            self.assertIn('192-168-0-10', rows[1])
            self.assertNotIn('75-163-143-71', rows[1])
            self.assertEqual(rows[2], fanart)
            self.assertEqual(rows[3], media)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
