import importlib
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from threading import Lock

from library_sync import isolate_modules


class ArtworkDeletionTests(unittest.TestCase):
    def setUp(self):
        isolate_modules(self)
        package = importlib.import_module('resources.lib')
        repo = Path(__file__).resolve().parents[1]
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.art = self.root / 'cache.jpg'
        self.art.write_bytes(b'artwork')
        self.plex_lock = Lock()
        self.removals = []
        for name in ('kodi_db', 'itemtypes'):
            for key in list(sys.modules):
                if key == 'resources.lib.' + name or key.startswith('resources.lib.' + name + '.'):
                    sys.modules.pop(key)
            module = types.ModuleType('resources.lib.' + name)
            module.__path__ = [str(repo / 'resources/lib' / name)]
            sys.modules[module.__name__] = module
            setattr(package, name, module)
        self.kodi_package = sys.modules['resources.lib.kodi_db']

        def connect(kind):
            conn = sqlite3.connect(str(self.root / (kind + '.db')))
            self.addCleanup(conn.close)
            return conn

        self.connect = connect
        with connect('video') as conn:
            conn.execute('CREATE TABLE art(media_id, media_type, url)')
            conn.execute("INSERT INTO art VALUES (1, 'movie', 'http://art')")
        with connect('texture') as conn:
            conn.execute('CREATE TABLE texture(url, cachedurl)')
            conn.execute("INSERT INTO texture VALUES ('http://art', 'cache.jpg')")

        def remove(path):
            self.removals.append((self.common.KODIDB_LOCK.locked(), self.plex_lock.locked()))
            os.remove(path)

        class PlexDB:
            def __init__(self, plexconn, lock):
                self.plexconn = plexconn
                self.cursor = plexconn.cursor()

        stubs = {
            'db': {'connect': connect, 'catch_operationalerrors': lambda func: func},
            'path_ops': {'translate_path': lambda path: str(self.art),
                         'remove': remove},
            'artwork_urls': {'normalize_plex_artwork_url': lambda url: url},
            'plex_db': {'PlexDB': PlexDB, 'PLEXDB_LOCK': self.plex_lock},
            'timing': {}, 'app': {},
        }
        for name, attributes in stubs.items():
            module = types.ModuleType('resources.lib.' + name)
            vars(module).update(attributes)
            sys.modules[module.__name__] = module
            setattr(package, name, module)
        self.common = importlib.import_module('resources.lib.kodi_db.common')

        class VideoDB(self.common.KodiDBBase):
            db_kind = 'video'

        self.video_class = VideoDB
        self.kodi_package.KodiVideoDB = VideoDB
        self.kodi_package.KODIDB_LOCK = self.common.KODIDB_LOCK
        item_common = importlib.import_module('resources.lib.itemtypes.common')
        self.item_class = item_common.ItemBase

    def assert_texture_removed(self):
        self.assertFalse(self.art.exists())
        self.assertEqual(self.removals, [(False, False)])
        with self.connect('texture') as conn:
            self.assertEqual(conn.execute('SELECT * FROM texture').fetchall(), [])

    def test_direct_db_context_removes_cache_after_lock_release(self):
        with self.video_class(texture_db=True) as context:
            context.delete_artwork(1, 'movie')
            self.assertTrue(self.art.exists())
        self.assert_texture_removed()

    def test_item_context_removes_cache_after_both_locks_release(self):
        with self.item_class(0) as context:
            context.kodidb.delete_artwork(1, 'movie')
            self.assertTrue(self.art.exists())
        self.assert_texture_removed()

    def test_rollback_preserves_cache_and_texture_row(self):
        for factory in (lambda: self.video_class(texture_db=True),
                        lambda: self.item_class(0)):
            with self.subTest(factory=factory):
                with self.assertRaises(ValueError):
                    with factory() as context:
                        getattr(context, 'kodidb', context).delete_artwork(1, 'movie')
                        raise ValueError('Abort transaction')
                self.assertTrue(self.art.exists())
                self.assertEqual(self.removals, [])
                with self.connect('texture') as conn:
                    self.assertEqual(conn.execute('SELECT cachedurl FROM texture').fetchall(),
                                     [('cache.jpg',)])
