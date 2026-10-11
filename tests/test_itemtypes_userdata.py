"""update_userdata() reports whether it changed a playstate in the Kodi DB"""
import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
ITEMTYPES_PATH = REPO_ROOT / 'resources' / 'lib' / 'itemtypes'
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class FakeAPI(object):
    def __init__(self, xml):
        self.xml = xml
        self.plex_id = xml['plex_id']

    def resume_point(self):
        return self.xml.get('resume')

    def runtime(self):
        return 8178

    def viewcount(self):
        return self.xml.get('viewcount')

    def lastplayed(self):
        return self.xml.get('lastplayed')

    def userrating(self):
        return self.xml.get('userrating', 0)


class FakeKodiDB(object):
    """Kodi DB holding one playstate per file (or song) id"""
    kodiconn = cursor = artconn = artcursor = None

    def __init__(self, playstates, ratings=None):
        self.playstates = dict(playstates)
        self.ratings = dict(ratings or {})
        self.writes = []

    def playstate_matches(self, file_id, *playstate):
        return self.playstates.get(file_id) == playstate

    def set_resume(self, file_id, *playstate):
        self.writes.append(file_id)
        self.playstates[file_id] = playstate

    def playcount_matches(self, kodi_id, playcount, lastplayed):
        return self.playstates.get(kodi_id) == (playcount, lastplayed)

    def set_playcount(self, playcount, lastplayed, kodi_id):
        self.writes.append(kodi_id)
        self.playstates[kodi_id] = (playcount, lastplayed)

    def update_userrating(self, kodi_id, kodi_type, userrating):
        """Like the Kodi DBs: True only if the rating differed"""
        if self.ratings.get((kodi_type, kodi_id), 0) == userrating:
            return False
        self.ratings[(kodi_type, kodi_id)] = userrating
        return True


def stub(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def load_itemtypes():
    """Imports itemtypes.movies, .tvshows and .music without Kodi"""
    modules = {}
    for name in list(sys.modules):
        if name.startswith('resources.lib.itemtypes'):
            sys.modules.pop(name)
    importlib.import_module('resources.lib')
    package = types.ModuleType('resources.lib.itemtypes')
    package.__path__ = [str(ITEMTYPES_PATH)]
    sys.modules['resources.lib.itemtypes'] = package
    lock = SimpleNamespace(acquire=lambda: None, release=lambda: None)
    stub('resources.lib.plex_db', PlexDB=object, PLEXDB_LOCK=lock)
    stub('resources.lib.kodi_db', KodiVideoDB=object, KodiMusicDB=object,
         KODIDB_LOCK=lock)
    stub('resources.lib.db')
    stub('resources.lib.timing')
    stub('resources.lib.app')
    stub('resources.lib.plex_functions')
    stub('resources.lib.plex_api', API=FakeAPI)
    stub('resources.lib.path_ops', append_os_sep=lambda path: path)
    stub('resources.lib.variables', PLEX_TYPE_EPISODE='episode',
         PLEX_TYPE_SONG='song')
    for name in ('movies', 'tvshows', 'music'):
        modules[name] = importlib.import_module(
            'resources.lib.itemtypes.%s' % name)
    return modules


def restore_attributes(module, attributes):
    for name in list(vars(module)):
        if name not in attributes:
            delattr(module, name)
    for name, value in attributes.items():
        setattr(module, name, value)


class UpdateUserdataChangeTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        package = importlib.import_module('resources.lib')
        self.addCleanup(restore_attributes, package, dict(vars(package)))
        self.modules = load_itemtypes()

    def item(self, module, cls, db_item, playstates, ratings=None):
        typus = getattr(self.modules[module], cls)(
            None,
            plexdb=SimpleNamespace(
                item_by_id=lambda plex_id, plex_type: db_item,
                plexconn=None, cursor=None),
            kodidb=FakeKodiDB(playstates, ratings))
        return typus

    def test_movie_with_unchanged_playstate_is_not_a_change(self):
        movie = self.item('movies', 'Movie',
                          {'kodi_fileid': 1, 'kodi_id': 1, 'kodi_type': 'movie'},
                          {1: (100.0, 8178, 2, '2026-10-01 12:00:00')})
        xml = {'plex_id': 5, 'resume': 100.0, 'viewcount': 2,
               'lastplayed': '2026-10-01 12:00:00'}

        self.assertTrue(movie.update_userdata(xml, 'movie'))
        self.assertFalse(movie.userdata_changed)

    def test_movie_with_new_playstate_is_a_change(self):
        movie = self.item('movies', 'Movie',
                          {'kodi_fileid': 1, 'kodi_id': 1, 'kodi_type': 'movie'},
                          {1: (None, 8178, None, None)})
        xml = {'plex_id': 5, 'resume': None, 'viewcount': 1,
               'lastplayed': '2026-10-01 12:00:00'}

        self.assertTrue(movie.update_userdata(xml, 'movie'))
        self.assertTrue(movie.userdata_changed)
        self.assertEqual(movie.kodidb.playstates[1],
                         (None, 8178, 1, '2026-10-01 12:00:00'))

    def test_episode_checks_both_kodi_files(self):
        playstate = (None, 8178, 1, '2026-10-01 12:00:00')
        db_item = {'kodi_fileid': 1, 'kodi_fileid_2': 2, 'kodi_id': 1,
                   'kodi_type': 'episode'}
        xml = {'plex_id': 5, 'resume': None, 'viewcount': 1,
               'lastplayed': '2026-10-01 12:00:00'}
        episode = self.item('tvshows', 'Episode', db_item,
                            {1: playstate, 2: playstate})
        self.assertTrue(episode.update_userdata(xml, 'episode'))
        self.assertFalse(episode.userdata_changed)
        self.assertEqual(episode.kodidb.writes, [1, 2])

        # Only the second (add-on path) file is out of date
        episode = self.item('tvshows', 'Episode', db_item,
                            {1: playstate, 2: (None, 8178, None, None)})
        self.assertTrue(episode.update_userdata(xml, 'episode'))
        self.assertTrue(episode.userdata_changed)

    def test_song_playcount_change_is_a_change(self):
        db_item = {'kodi_id': 3, 'kodi_type': 'song'}
        xml = {'plex_id': 5, 'viewcount': 4, 'lastplayed': '2026-10-01'}
        song = self.item('music', 'Song', db_item, {3: (4, '2026-10-01')})
        self.assertTrue(song.update_userdata(xml, 'song'))
        self.assertFalse(song.userdata_changed)

        song = self.item('music', 'Song', db_item, {3: (3, '2026-09-01')})
        self.assertTrue(song.update_userdata(xml, 'song'))
        self.assertTrue(song.userdata_changed)


    def test_userrating_change_is_a_change(self):
        playstate = (None, 8178, 1, '2026-10-01 12:00:00')
        xml = {'plex_id': 5, 'resume': None, 'viewcount': 1,
               'lastplayed': '2026-10-01 12:00:00', 'userrating': 8}
        cases = (
            ('movies', 'Movie', 'movie',
             {'kodi_fileid': 1, 'kodi_id': 1, 'kodi_type': 'movie'}),
            ('tvshows', 'Episode', 'episode',
             {'kodi_fileid': 1, 'kodi_fileid_2': None, 'kodi_id': 1,
              'kodi_type': 'episode'}),
            ('tvshows', 'Season', 'season',
             {'kodi_id': 1, 'kodi_type': 'season'}),
            ('music', 'Album', 'album',
             {'kodi_id': 1, 'kodi_type': 'album'}),
        )
        song_xml = {'plex_id': 5, 'viewcount': 1,
                    'lastplayed': '2026-10-01 12:00:00', 'userrating': 8}
        cases += (('music', 'Song', 'song',
                   {'kodi_id': 1, 'kodi_type': 'song'}),)
        for module, cls, plex_type, db_item in cases:
            with self.subTest(cls):
                data = song_xml if cls == 'Song' else xml
                states = {1: (1, '2026-10-01 12:00:00')} if cls == 'Song' \
                    else {1: playstate}
                item = self.item(module, cls, db_item, states)
                self.assertTrue(item.update_userdata(data, plex_type))
                self.assertTrue(item.userdata_changed)
                self.assertEqual(
                    item.kodidb.ratings[(db_item['kodi_type'], 1)], 8)

                item = self.item(module, cls, db_item, states,
                                 {(db_item['kodi_type'], 1): 8})
                self.assertTrue(item.update_userdata(data, plex_type))
                self.assertFalse(item.userdata_changed)


if __name__ == '__main__':
    unittest.main()
