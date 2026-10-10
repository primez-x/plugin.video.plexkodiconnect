import importlib
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
KODI_DB_PATH = REPO_ROOT / 'resources' / 'lib' / 'kodi_db'
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_video_module():
    for module_name in (
            'xbmc',
            'xbmcvfs',
            'xbmcaddon',
            'resources.lib.app',
            'resources.lib.db',
            'resources.lib.exceptions',
            'resources.lib.path_ops',
            'resources.lib.variables',
            'resources.lib.kodi_db',
            'resources.lib.kodi_db.common',
            'resources.lib.kodi_db.video'):
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType('xbmc')
    xbmc.ISO_639_1 = 'iso-639-1'
    xbmc.getCondVisibility = lambda condition: False
    xbmc.getInfoLabel = lambda label: '21.3'
    xbmc.getLanguage = lambda language: 'en_US'
    sys.modules['xbmc'] = xbmc

    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.translatePath = lambda path: path
    xbmcvfs.exists = lambda path: 0
    sys.modules['xbmcvfs'] = xbmcvfs

    xbmcaddon = types.ModuleType('xbmcaddon')

    class Addon(object):
        def __init__(self, addon_id=None):
            self.addon_id = addon_id

        def getAddonInfo(self, key):
            return {
                'version': '3.12.54',
                'path': str(REPO_ROOT),
                'profile': 'special://profile/addon_data/test',
            }.get(key, '')

        def getSetting(self, key):
            return '0' if key == 'companionPort' else ''

        def setSetting(self, key, value):
            return None

    xbmcaddon.Addon = Addon
    sys.modules['xbmcaddon'] = xbmcaddon

    app = types.ModuleType('resources.lib.app')
    app.APP = SimpleNamespace(
        monitor=SimpleNamespace(waitForAbort=lambda timeout: False),
    )
    sys.modules['resources.lib.app'] = app

    kodi_db = types.ModuleType('resources.lib.kodi_db')
    kodi_db.__path__ = [str(KODI_DB_PATH)]
    sys.modules['resources.lib.kodi_db'] = kodi_db
    return importlib.import_module('resources.lib.kodi_db.video')


def restore_attributes(module, attributes):
    for name in list(vars(module)):
        if name not in attributes:
            delattr(module, name)
    for name, value in attributes.items():
        setattr(module, name, value)


class KodiVideoDBTests(unittest.TestCase):
    def setUp(self):
        # load_video_module() imports e.g. the real variables module with an
        # untranslated special:// profile. Restore all modules afterwards so
        # later tests never write to a literal special:/ folder.
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        package = sys.modules.get('resources.lib')
        if package is not None:
            self.addCleanup(restore_attributes, package, dict(vars(package)))

    def test_failed_connect_releases_the_lock_and_closes_connections(self):
        video = load_video_module()
        common = sys.modules['resources.lib.kodi_db.common']
        opened = []

        def connect(kind):
            if kind == 'texture':
                raise sqlite3.OperationalError('database is locked')
            conn = sqlite3.connect(':memory:')
            opened.append(conn)
            return conn

        common.db.connect = connect
        with self.assertRaises(sqlite3.OperationalError):
            with video.KodiVideoDB(texture_db=True):
                pass

        self.assertFalse(common.KODIDB_LOCK.locked())
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute('SELECT 1')

    def test_set_watched_removes_resume_and_preserves_existing_history(self):
        video = load_video_module()
        conn = sqlite3.connect(':memory:')
        conn.executescript('''
            CREATE TABLE files (
                idFile INTEGER PRIMARY KEY,
                playCount INTEGER,
                lastPlayed TEXT
            );
            CREATE TABLE bookmark (
                idFile INTEGER,
                timeInSeconds REAL,
                totalTimeInSeconds REAL
            );
            INSERT INTO files VALUES (1, 7, '2026-08-01 12:00:00');
            INSERT INTO files VALUES (2, NULL, NULL);
            INSERT INTO bookmark VALUES (1, 45.0, 1200.0);
            INSERT INTO bookmark VALUES (2, 5.0, 1200.0);
        ''')
        database = object.__new__(video.KodiVideoDB)
        database.cursor = conn.cursor()

        database.set_watched(1, '2026-08-26 12:00:00')
        database.set_watched(2, '2026-08-26 12:00:00')
        conn.commit()

        self.assertEqual(
            conn.execute('SELECT playCount, lastPlayed FROM files WHERE idFile = 1')
            .fetchone(),
            (7, '2026-08-01 12:00:00'),
        )
        self.assertEqual(
            conn.execute('SELECT playCount, lastPlayed FROM files WHERE idFile = 2')
            .fetchone(),
            (1, '2026-08-26 12:00:00'),
        )
        self.assertEqual(
            conn.execute('SELECT COUNT(*) FROM bookmark').fetchone()[0],
            0,
        )
        conn.close()

    def test_playstate_matches_only_what_set_resume_would_write(self):
        video = load_video_module()
        conn = sqlite3.connect(':memory:')
        conn.executescript('''
            CREATE TABLE files (
                idFile INTEGER PRIMARY KEY,
                playCount INTEGER,
                lastPlayed TEXT
            );
            CREATE TABLE bookmark (
                idBookmark INTEGER PRIMARY KEY,
                idFile INTEGER,
                timeInSeconds REAL,
                totalTimeInSeconds REAL,
                thumbNailImage TEXT,
                player TEXT,
                playerState TEXT,
                type INTEGER
            );
            INSERT INTO files VALUES (1, NULL, NULL);
            INSERT INTO files VALUES (2, 3, '2026-08-01 12:00:00');
        ''')
        database = object.__new__(video.KodiVideoDB)
        database.cursor = conn.cursor()
        # A file without bookmarks and no playstate already matches
        self.assertTrue(database.playstate_matches(1, 0, 8178, 0, None))
        self.assertFalse(database.playstate_matches(1, 5653.428, 8178, 0, None))

        database.set_resume(1, 5653.428, 8178, 0, None)
        self.assertTrue(database.playstate_matches(1, 5653.428, 8178, 0, None))
        self.assertFalse(database.playstate_matches(1, 5700.0, 8178, 0, None))
        self.assertFalse(database.playstate_matches(1, 5653.428, 8178, 1, None))

        self.assertTrue(database.playstate_matches(
            2, None, 8178, 3, '2026-08-01 12:00:00'))
        self.assertFalse(database.playstate_matches(
            2, None, 8178, 3, '2026-08-02 12:00:00'))
        # Unknown Kodi file: never claim a match
        self.assertFalse(database.playstate_matches(3, None, 8178, 0, None))
        conn.close()

    def test_uniqueid_lookup_is_batched_and_scoped_to_the_media_type(self):
        video = load_video_module()
        conn = sqlite3.connect(':memory:')
        conn.executescript('''
            CREATE TABLE uniqueid (
                uniqueid_id INTEGER PRIMARY KEY,
                media_id INTEGER,
                media_type TEXT,
                value TEXT,
                type TEXT
            );
            CREATE INDEX ix_uniqueid2 ON uniqueid (media_type, value);
            INSERT INTO uniqueid VALUES (1, 7, 'movie', '603', 'tmdb');
            INSERT INTO uniqueid VALUES (2, 7, 'movie', 'tt0133093', 'imdb');
            INSERT INTO uniqueid VALUES (3, 9, 'movie', '603', 'tmdb');
            INSERT INTO uniqueid VALUES (4, 3, 'tvshow', '603', 'tmdb');
            INSERT INTO uniqueid VALUES (5, 8, 'movie', '81189', 'tvdb');
        ''')
        database = object.__new__(video.KodiVideoDB)
        database.cursor = conn.cursor()
        video.UNIQUEID_QUERY_BATCH_SIZE = 2

        result = database.kodi_ids_by_uniqueids(
            'movie', ['603', 'tt0133093', '603', '81189', 'unknown'])

        self.assertEqual(
            {key: sorted(kodi_ids) for key, kodi_ids in result.items()},
            {
                ('tmdb', '603'): [7, 9],
                ('imdb', 'tt0133093'): [7],
                ('tvdb', '81189'): [8],
            },
        )
        self.assertEqual(
            database.kodi_ids_by_uniqueids('tvshow', ['603']),
            {('tmdb', '603'): [3]},
        )
        self.assertEqual(database.kodi_ids_by_uniqueids('movie', []), {})
        conn.close()


if __name__ == '__main__':
    unittest.main()
