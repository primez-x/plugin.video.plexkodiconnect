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
    stub('resources.lib.timing', plex_date_to_kodi=lambda timestamp: timestamp)
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

    def item(self, module, cls, db_item, playstates, ratings=None,
             file_episodes=None):
        file_episodes = file_episodes or {}
        typus = getattr(self.modules[module], cls)(
            None,
            plexdb=SimpleNamespace(
                item_by_id=lambda plex_id, plex_type: db_item,
                episode_ids_by_file=lambda file_id: file_episodes.get(
                    file_id, []),
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
        self.assertEqual(episode.kodidb.writes, [])

        # Only the second (add-on path) file is out of date
        episode = self.item('tvshows', 'Episode', db_item,
                            {1: playstate, 2: (None, 8178, None, None)})
        self.assertTrue(episode.update_userdata(xml, 'episode'))
        self.assertTrue(episode.userdata_changed)
        self.assertEqual(episode.kodidb.writes, [2])

    # S01E03E04: Plex episodes 5 and 6 are Kodi episodes 1 and 2, which share
    # Kodi file 7. Kodi holds just one playstate for the file
    WATCHED = {'resume': 0.0, 'viewcount': 1,
               'lastplayed': '2026-10-01 12:00:00'}
    UNWATCHED = {'resume': 0.0, 'viewcount': 0, 'lastplayed': None}
    RESUMED = {'resume': 600.0, 'viewcount': 0,
               'lastplayed': '2026-10-02 12:00:00'}

    def shared_file_sync(self, plex, kodi_state):
        """
        Full sync of both episodes sharing a file, given the Plex states
        {plex_id: xml}. Returns the Kodi items to inspect their end state
        """
        self.modules['tvshows'].PF.GetPlexMetadata = (
            lambda plex_id: [dict(plex[plex_id], plex_id=plex_id)])
        playstates = {7: kodi_state}
        items = []
        for plex_id, kodi_id in ((5, 1), (6, 2)):
            episode = self.item(
                'tvshows', 'Episode',
                {'kodi_fileid': 7, 'kodi_fileid_2': None, 'kodi_id': kodi_id,
                 'kodi_type': 'episode'},
                playstates, file_episodes={7: [5, 6]})
            episode.kodidb.playstates = playstates
            self.assertTrue(episode.update_userdata(
                dict(plex[plex_id], plex_id=plex_id), 'episode'))
            items.append(episode)
        return items, playstates[7]

    def test_unchanged_shared_file_is_not_a_change(self):
        merged = (0.0, 8178, 1, '2026-10-01 12:00:00')
        items, state = self.shared_file_sync(
            {5: self.WATCHED, 6: self.UNWATCHED}, merged)
        self.assertEqual([x.userdata_changed for x in items], [False, False])
        self.assertEqual(items[0].kodidb.writes, [])
        self.assertEqual(state, merged)

    def test_shared_file_stays_watched_via_either_episode(self):
        unwatched = (0.0, 8178, 0, None)
        for plex in ({5: self.WATCHED, 6: self.UNWATCHED},
                     {5: self.UNWATCHED, 6: self.WATCHED}):
            items, state = self.shared_file_sync(plex, unwatched)
            self.assertEqual(state, (0.0, 8178, 1, '2026-10-01 12:00:00'))
            # The next full sync has nothing left to do
            items, state = self.shared_file_sync(plex, state)
            self.assertEqual([x.userdata_changed for x in items],
                             [False, False])

    def test_shared_file_keeps_resume_point_of_either_episode(self):
        unwatched = (0.0, 8178, 0, None)
        resumed = (600.0, 8178, 0, '2026-10-02 12:00:00')
        for plex in ({5: self.RESUMED, 6: self.UNWATCHED},
                     {5: self.UNWATCHED, 6: self.RESUMED}):
            items, state = self.shared_file_sync(plex, unwatched)
            self.assertEqual(state, resumed)
            items, state = self.shared_file_sync(plex, state)
            self.assertEqual([x.userdata_changed for x in items],
                             [False, False])

    def test_shared_file_merges_watched_and_resumed_episodes(self):
        # E03 was watched, E04 started afterwards: watched, resume of E04
        expected = (600.0, 8178, 1, '2026-10-02 12:00:00')
        for plex in ({5: self.WATCHED, 6: self.RESUMED},
                     {5: self.RESUMED, 6: self.WATCHED}):
            items, state = self.shared_file_sync(
                plex, (0.0, 8178, 0, None))
            self.assertEqual(state, expected)

    def test_shared_file_is_left_alone_if_the_other_episode_is_unknown(self):
        kodi_state = (0.0, 8178, 1, '2026-10-01 12:00:00')
        episode = self.item(
            'tvshows', 'Episode',
            {'kodi_fileid': 7, 'kodi_fileid_2': None, 'kodi_id': 1,
             'kodi_type': 'episode'},
            {7: kodi_state}, file_episodes={7: [5, 6]})
        self.modules['tvshows'].PF.GetPlexMetadata = lambda plex_id: None
        self.assertTrue(episode.update_userdata(
            dict(self.UNWATCHED, plex_id=5), 'episode'))
        self.assertEqual(episode.kodidb.writes, [])
        self.assertFalse(episode.userdata_changed)
        self.assertEqual(episode.kodidb.playstates[7], kodi_state)

    def test_websocket_playstate_matches_what_full_sync_writes(self):
        # E04 (6) was watched. E03 (5) is now played on another device, up to
        # 300 seconds, at 2026-10-03 12:00:00
        playing = {'resume': 300, 'viewcount': 0,
                   'lastplayed': '2026-10-03 12:00:00'}
        self.modules['tvshows'].PF.GetPlexMetadata = (
            lambda plex_id: [dict(self.WATCHED, plex_id=plex_id)])
        episode = self.item(
            'tvshows', 'Episode',
            {'kodi_fileid': 7, 'kodi_fileid_2': None, 'kodi_id': 1,
             'kodi_type': 'episode'},
            {7: (0.0, 8178, 1, '2026-10-01 12:00:00')},
            file_episodes={7: [5, 6]})
        episode.update_playstate(False, 0, 300, 8178, 7, None,
                                 '2026-10-03 12:00:00', 5)
        websocket_state = episode.kodidb.playstates[7]
        self.assertEqual(websocket_state,
                         (300, 8178, 1, '2026-10-03 12:00:00'))

        # PMS then reports the same, and the full sync has nothing to change
        items, state = self.shared_file_sync(
            {5: playing, 6: self.WATCHED}, websocket_state)
        self.assertEqual([x.userdata_changed for x in items], [False, False])
        self.assertEqual(state, websocket_state)

    def test_websocket_finished_episode_keeps_watched_sibling_playcount(self):
        sibling = {'resume': 0.0, 'viewcount': 2,
                   'lastplayed': '2026-10-01 12:00:00'}
        self.modules['tvshows'].PF.GetPlexMetadata = (
            lambda plex_id: [dict(sibling, plex_id=plex_id)])
        episode = self.item(
            'tvshows', 'Episode',
            {'kodi_fileid': 7, 'kodi_fileid_2': None, 'kodi_id': 1,
             'kodi_type': 'episode'},
            {7: (0.0, 8178, 2, '2026-10-01 12:00:00')},
            file_episodes={7: [5, 6]})
        episode.update_playstate(True, 0, 8000, 8178, 7, None,
                                 '2026-10-03 12:00:00', 5)
        self.assertEqual(episode.kodidb.playstates[7],
                         (0, 8178, 2, '2026-10-03 12:00:00'))

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
