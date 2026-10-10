import importlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
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


OLD_SERVER = 'https://75-163-143-71.8040ad440812489a81c0accac657c560.plex.direct:32400'
NEW_SERVER = 'https://192-168-0-10.8040ad440812489a81c0accac657c560.plex.direct:32400'
OLD_ART = (
    OLD_SERVER + '/photo/:/transcode?width=1920&height=1920&minSize=1'
    '&upscale=0&url=/library/metadata/4488/thumb/1779675951&X-Plex-Token=t'
)


class StartupArtworkNormalizationTests(unittest.TestCase):
    """kodi_db.normalize_artwork_urls() runs once per (DB, PMS) identity."""

    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        package = importlib.import_module('resources.lib')
        saved = dict(vars(package))

        def restore():
            for name in list(vars(package)):
                if name not in saved:
                    delattr(package, name)
            for name, value in saved.items():
                setattr(package, name, value)
        self.addCleanup(restore)
        for name in ('app', 'artwork_urls', 'path_ops', 'utils', 'variables',
                     'kodi_db'):
            package.__dict__.pop(name, None)
            sys.modules.pop('resources.lib.%s' % name, None)

        self.profile = tempfile.TemporaryDirectory()
        self.addCleanup(self.profile.cleanup)
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE art(art_id INTEGER PRIMARY KEY, url TEXT)')
        self.conn.execute('INSERT INTO art(art_id, url) VALUES (1, ?)', (OLD_ART,))
        self.scans = []
        test = self

        class FakeKodiVideoDB(object):
            def __enter__(self):
                test.scans.append(True)
                self.cursor = test.conn.cursor()
                return self

            def __exit__(self, *args):
                test.conn.commit()

        def stub(name, **attributes):
            module = types.ModuleType(name)
            module.__dict__.update(attributes)
            sys.modules[name] = module
            return module

        stub('resources.lib.app', CONN=SimpleNamespace(server=None))
        stub('resources.lib.path_ops')
        stub('resources.lib.utils')
        stub('resources.lib.variables',
             DB_VIDEO_PATH='/kodi/MyVideos131.db',
             ADDON_PROFILE=self.profile.name,
             **{'PLEX_TYPE_%s' % kind: kind.lower() for kind in (
                 'MOVIE', 'SHOW', 'SEASON', 'EPISODE', 'ARTIST', 'ALBUM',
                 'SONG')})
        stub('resources.lib.kodi_db.common', KODIDB_LOCK=None)
        stub('resources.lib.kodi_db.video', KodiVideoDB=FakeKodiVideoDB)
        stub('resources.lib.kodi_db.music', KodiMusicDB=object)
        stub('resources.lib.kodi_db.texture', KodiTextureDB=object)
        kodi_db_dir = REPO_ROOT / 'resources' / 'lib' / 'kodi_db'
        spec = importlib.util.spec_from_file_location(
            'resources.lib.kodi_db', kodi_db_dir / '__init__.py',
            submodule_search_locations=[str(kodi_db_dir)])
        self.kodi_db = importlib.util.module_from_spec(spec)
        sys.modules['resources.lib.kodi_db'] = self.kodi_db
        spec.loader.exec_module(self.kodi_db)
        self.variables = sys.modules['resources.lib.variables']

    def _art_url(self):
        return self.conn.execute('SELECT url FROM art WHERE art_id = 1').fetchone()[0]

    def test_unchanged_server_skips_the_table_scan_on_later_startups(self):
        self.assertEqual(self.kodi_db.normalize_artwork_urls(NEW_SERVER), 1)
        self.assertTrue(self._art_url().startswith(NEW_SERVER))

        # Next Kodi start, same PMS and Kodi DB: no full-table scan
        self.assertEqual(self.kodi_db.normalize_artwork_urls(NEW_SERVER), 0)
        self.assertEqual(len(self.scans), 1)

    def test_changed_server_or_kodi_db_scans_again(self):
        self.kodi_db.normalize_artwork_urls(NEW_SERVER)
        self.assertEqual(self.kodi_db.normalize_artwork_urls(OLD_SERVER), 1)
        self.assertTrue(self._art_url().startswith(OLD_SERVER))
        self.assertEqual(len(self.scans), 2)

        # Kodi upgrade -> new video DB file, maybe migrated from an old one
        self.variables.DB_VIDEO_PATH = '/kodi/MyVideos137.db'
        self.kodi_db.normalize_artwork_urls(OLD_SERVER)
        self.assertEqual(len(self.scans), 3)

    def test_interrupted_scan_leaves_no_marker_behind(self):
        self.kodi_db.normalize_artwork_urls(NEW_SERVER)

        def interrupted(cursor, server=None):
            raise RuntimeError('Kodi shut down mid-scan')
        real_scan = self.kodi_db.artwork_urls.normalize_artwork_table_urls
        self.kodi_db.artwork_urls.normalize_artwork_table_urls = interrupted
        with self.assertRaises(RuntimeError):
            self.kodi_db.normalize_artwork_urls(OLD_SERVER)
        self.kodi_db.artwork_urls.normalize_artwork_table_urls = real_scan

        # Back on the previous server: the old marker must not skip the scan
        self.assertEqual(self.kodi_db.normalize_artwork_urls(NEW_SERVER), 0)
        self.assertEqual(len(self.scans), 3)
        self.assertTrue(os.path.exists(os.path.join(
            self.profile.name,
            self.kodi_db.artwork_urls.NORMALIZED_MARKER_FILENAME)))

    def test_no_server_does_nothing(self):
        self.assertEqual(self.kodi_db.normalize_artwork_urls(None), 0)
        self.assertEqual(self.scans, [])


if __name__ == '__main__':
    unittest.main()
