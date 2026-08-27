import importlib
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


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


class KodiVideoDBTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
