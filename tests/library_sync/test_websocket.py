import unittest
from types import SimpleNamespace

from . import isolate_modules, load_library_sync_module


ITEMS = {
    100: {'plex_type': 'movie', 'kodi_id': 1, 'kodi_type': 'movie',
          'kodi_fileid': 11},
    200: {'plex_type': 'movie', 'kodi_id': 2, 'kodi_type': 'movie',
          'kodi_fileid': 22},
}
DURATIONS = {100: 1000, 200: 4000}


class FakePlexDB(object):
    def __init__(self, lock=True):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def item_by_id(self, plex_id, plex_type=None):
        item = ITEMS.get(plex_id)
        return dict(item) if item else None


class FakeAPI(object):
    def __init__(self, xml):
        self.plex_id = xml

    def runtime(self):
        return DURATIONS[self.plex_id]

    def viewcount(self):
        return 0


class WebsocketPlaystateSessionTests(unittest.TestCase):
    def setUp(self):
        isolate_modules(self)
        self.writes = []
        self.metadata_requests = []
        self.settings = {'plex_serverowned': 'false'}
        self.pms_sessions = {}
        writes = self.writes

        class FakeItemtype(object):
            def __init__(self, last_sync):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def update_playstate(self, mark_played, view_count, resume,
                                 duration, kodi_fileid, kodi_fileid_2,
                                 lastplayed):
                writes.append((kodi_fileid, resume, duration, mark_played))

        def get_metadata(plex_id):
            self.metadata_requests.append(plex_id)
            return [plex_id]

        def cast(func, value):
            return None if value is None else func(value)

        self.app = SimpleNamespace(
            PLAYSTATE=SimpleNamespace(player_states={
                0: {'plex_id': None}, 1: {'plex_id': None},
                2: {'plex_id': None}}),
            ACCOUNT=SimpleNamespace(plex_token='token', plex_user_id='7',
                                    plex_username='me'),
            SYNC=SimpleNamespace(sections=[]))
        self.websocket = load_library_sync_module('websocket', {
            'resources.lib.library_sync.common': {
                'update_kodi_library': lambda **kwargs: None,
                'PLAYLIST_SYNC_ENABLED': False},
            'resources.lib.library_sync.additional_metadata': {
                'ProcessMetadataTask': object},
            'resources.lib.plex_api': {'API': FakeAPI},
            'resources.lib.plex_db': {'PlexDB': FakePlexDB},
            'resources.lib.kodi_db': {},
            'resources.lib.backgroundthread': {},
            'resources.lib.plex_functions': {
                'GetPlexMetadata': get_metadata,
                'GetPMSStatus': lambda token: dict(self.pms_sessions)},
            'resources.lib.itemtypes': {
                'ITEMTYPE_FROM_KODITYPE': {'movie': FakeItemtype}},
            'resources.lib.artwork': {},
            'resources.lib.utils': {
                'settings': lambda key: self.settings.get(key, ''),
                'cast': cast},
            'resources.lib.timing': {'unix_timestamp': lambda: 0},
            'resources.lib.variables': {
                'PLEX_TYPE_EPISODE': 'episode',
                'IGNORE_SECONDS_AT_START': 60,
                'MARK_PLAYED_AT': 0.9},
            'resources.lib.app': {
                'PLAYSTATE': self.app.PLAYSTATE,
                'ACCOUNT': self.app.ACCOUNT,
                'SYNC': self.app.SYNC},
        })

    def play(self, session_key, plex_id, offset_ms, state='playing'):
        self.websocket.process_playing([{
            'state': state,
            'sessionKey': session_key,
            'ratingKey': str(plex_id),
            'viewOffset': offset_ms,
        }])

    def test_reused_session_key_never_writes_old_items_playstate(self):
        self.play('1', 100, 300000)
        # PMS restarted and handed out session key 1 again for another item
        self.play('1', 200, 600000)

        self.assertEqual(self.writes, [
            (11, 300.0, 1000, False),
            (22, 600.0, 4000, False),
        ])
        self.assertEqual(list(self.websocket.PLAYSTATE_SESSIONS),
                         [('1', 200)])

    def test_updates_for_the_same_session_reuse_cached_metadata(self):
        self.play('1', 100, 300000)
        self.play('1', 100, 400000, state='paused')

        self.assertEqual(self.metadata_requests, [100])
        self.assertEqual([write[1] for write in self.writes], [300.0, 400.0])

    def test_stopped_drops_session_without_writing(self):
        self.play('1', 100, 300000)
        self.play('1', 100, 900000, state='stopped')

        self.assertEqual(self.websocket.PLAYSTATE_SESSIONS, {})
        # The last playing/paused update already carried the final playstate
        self.assertEqual(self.writes, [(11, 300.0, 1000, False)])

    def test_stopped_for_other_item_keeps_session(self):
        self.play('1', 100, 300000)
        self.play('1', 200, 0, state='stopped')

        self.assertEqual(list(self.websocket.PLAYSTATE_SESSIONS),
                         [('1', 100)])

    def test_session_map_is_bounded_and_evicts_least_recently_updated(self):
        limit = self.websocket.MAX_PLAYSTATE_SESSIONS
        self.play('active', 100, 300000)
        for key in range(limit + 10):
            self.play(str(key), 200, 600000)
            # A session that keeps reporting progress must stay cached
            self.play('active', 100, 300000 + key * 1000)

        sessions = self.websocket.PLAYSTATE_SESSIONS
        self.assertEqual(len(sessions), limit)
        self.assertIn(('active', 100), sessions)
        self.assertNotIn(('0', 200), sessions)
        self.assertIn((str(limit + 9), 200), sessions)
        self.assertEqual(self.metadata_requests.count(100), 1)

    def test_owned_server_session_of_other_user_is_skipped(self):
        self.settings['plex_serverowned'] = 'true'
        self.pms_sessions = {
            '1': {'userId': '8', 'username': 'other', 'ratingKey': '100'},
            '2': {'userId': '7', 'username': 'me', 'ratingKey': '200'},
        }
        self.play('1', 100, 300000)
        self.play('2', 200, 600000)

        self.assertEqual(self.writes, [(22, 600.0, 4000, False)])


if __name__ == '__main__':
    unittest.main()
