"""Failure cases: invalid IDs, unsafe matches, partial data, races and DB damage."""
import importlib.util
import os
import re
import socket
import sqlite3
import sys
import tempfile
import time
import types
import unittest
import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)) if str(ROOT) not in sys.path else None
KEY = '5d776883ebdf2200209c104e'
OTHER = '5d776883ebdf2200209c104f'
MOVIE = 'plex://movie/' + KEY
ACCOUNT = sha256(b'account-one').hexdigest()
SECOND = sha256(b'account-two').hexdigest()


def metadata(key=KEY, media_type='movie', **attributes):
    values = dict(type=media_type, ratingKey=key,
                  guid='plex://%s/%s' % (media_type, key), title='Same title')
    values.update(attributes)
    row = ET.Element('Video', values)
    ET.SubElement(row, 'Guid', id='tmdb://603')
    return row


class MembershipTests(unittest.TestCase):
    def setUp(self):
        import resources.lib
        self.package = resources.lib
        attributes = vars(self.package).copy()
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)

        def restore():
            vars(self.package).clear()
            vars(self.package).update(attributes)
        self.addCleanup(restore)
        self.properties = {'plex_token': 'account-one', 'PKC.Account.Hash': ACCOUNT}
        self.on_window = None
        self.before_window = None
        self.window_writes = []
        self.settings = {}
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE uniqueid(uniqueid_id INTEGER PRIMARY KEY,
                media_id INTEGER, media_type TEXT, value TEXT, type TEXT,
                UNIQUE(media_id, media_type, type));
            CREATE TABLE movie(idMovie INTEGER PRIMARY KEY, playCount INTEGER,
                resume REAL, userrating INTEGER, c09 INTEGER);
            INSERT INTO movie VALUES(7, 3, 11.5, 8, 1);
            INSERT INTO movie VALUES(9, 0, 0, 4, 2);
            INSERT INTO uniqueid VALUES(1, 7, 'movie', '603', 'tmdb');
            INSERT INTO uniqueid VALUES(2, 9, 'movie', '603', 'tmdb');
            INSERT INTO uniqueid VALUES(3, 7, 'movie', 'tt0133093', 'imdb');
        ''')
        self.connections = 0
        self.exact = {}
        self.synced = {('movie', 7): 41, ('movie', 9): 42}
        self.after_match = None
        self.db_error = None
        self.network = patch.object(socket.socket, 'connect',
                                    side_effect=AssertionError('Network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

        def module(name, **values):
            result = types.ModuleType(name)
            vars(result).update(values)
            sys.modules[name] = result
            parent, _, leaf = name.rpartition('.')
            if parent in sys.modules:
                setattr(sys.modules[parent], leaf, result)
            return result

        def window(name, value=None, clear=False):
            if (clear or value is not None) and self.before_window:
                self.before_window(name, value, clear)
            if clear:
                self.properties.pop(name, None)
            elif value is not None:
                self.properties[name] = value
            else:
                return self.properties.get(name, '')
            self.window_writes.append((name, value, clear))
            if self.on_window:
                self.on_window(name, value, clear)

        def setting(name, value=None):
            if value is not None:
                self.settings[name] = value
            return self.settings.get(name, '')

        module('xbmcgui', ListItem=object)
        module('resources.lib.utils', window=window, settings=setting,
               cast=lambda cls, value: cls(value) if value is not None else None,
               REGEX_IMDB=re.compile(r'tt\d+'), REGEX_TMDB=re.compile(r'tmdb://(\d+)'),
               REGEX_TVDB=re.compile(r'tvdb://(\d+)'), REGEX_ANIDB=re.compile(r'anidb://(\d+)'))
        for name in ('timing', 'plex_functions', 'widgets'):
            module('resources.lib.' + name)
        module('resources.lib.app', __path__=[str(ROOT / 'resources/lib/app')])
        self.profile = tempfile.TemporaryDirectory()
        self.addCleanup(self.profile.cleanup)
        module('resources.lib.variables', ADDON_PROFILE=self.profile.name,
               PLEX_TYPE_MOVIE='movie', PLEX_TYPE_SHOW='show', PLEX_TYPE_CLIP='clip',
               KODI_TYPE_MOVIE='movie', KODI_TYPE_SHOW='tvshow',
               KODITYPE_FROM_PLEXTYPE={'movie': 'movie', 'show': 'tvshow',
                                      'season': 'season', 'episode': 'episode'})
        owner = self

        class PlexDB:
            def __init__(self, lock=False):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def items_by_guid(self, guid, plex_type):
                return [{'plex_id': value} for value in owner.exact.get(guid, ())]
            def item_by_kodi_id(self, kodi_id, kodi_type):
                value = owner.synced.get((kodi_type, kodi_id))
                return {'plex_id': value} if value is not None else None
            def item_by_id(self, plex_id, plex_type):
                for (kodi_type, kodi_id), value in owner.synced.items():
                    if value == plex_id:
                        return dict(kodi_id=kodi_id, section_id=1, last_sync=0,
                                    checksum=0)

        class KodiVideoDB:
            def __init__(self, lock=True):
                self.cursor = owner.db.cursor()
            def __enter__(self):
                owner.connections += 1
                if owner.db_error:
                    raise owner.db_error
                return self
            def __exit__(self, exc_type, *args):
                if exc_type:
                    owner.db.rollback()
                else:
                    owner.db.commit()
                return False
            def kodi_ids_by_uniqueids(self, kodi_type, values):
                result = {}
                for kodi_id, provider, value in self.cursor.execute(
                        'SELECT media_id,type,value FROM uniqueid WHERE media_type=?',
                        (kodi_type,)):
                    if value in values:
                        result.setdefault((provider, value), []).append(kodi_id)
                return result
            def add_uniqueid(self, kodi_id, kodi_type, value, provider):
                self.cursor.execute('INSERT INTO uniqueid(media_id,media_type,value,type) '
                                    'VALUES(?,?,?,?)', (kodi_id, kodi_type, value, provider))
                return self.cursor.lastrowid

        module('resources.lib.plex_db', PlexDB=PlexDB)
        module('resources.lib.kodi_db', KodiVideoDB=KodiVideoDB)
        module('resources.lib.plex_api',
                         __path__=[str(ROOT / 'resources/lib/plex_api')])
        for name in ('artwork', 'file', 'media', 'user', 'playback'):
            cls = name.title()
            module('resources.lib.plex_api.' + name, **{cls: type(cls, (), {})})
        self.base = self.load('resources.lib.plex_api.base', 'resources/lib/plex_api/base.py')
        self.api = self.load('resources.lib.plex_api', 'resources/lib/plex_api/__init__.py')
        real_mass_api = self.api.mass_api
        def match(xml, **kwargs):
            result = real_mass_api(xml, **kwargs)
            if self.after_match:
                self.after_match(result)
            return result
        self.api.mass_api = match
        sys.modules.pop('resources.lib.discover_cache', None)
        if hasattr(self.package, 'discover_cache'):
            delattr(self.package, 'discover_cache')
        sys.modules.pop('resources.lib.plex_membership', None)
        if hasattr(self.package, 'plex_membership'):
            delattr(self.package, 'plex_membership')

    def load(self, name, path):
        path = ROOT / path
        self.assertTrue(path.exists(), 'Required projection module is absent: %s' % path)
        spec = importlib.util.spec_from_file_location(name, path)
        result = importlib.util.module_from_spec(spec)
        sys.modules[name] = result
        spec.loader.exec_module(result)
        setattr(sys.modules[name.rpartition('.')[0]], name.rpartition('.')[2], result)
        return result

    def membership(self):
        return self.load('resources.lib.plex_membership', 'resources/lib/plex_membership.py')

    def guids(self, kind='Watchlist'):
        return self.properties.get('PKC.%s.Guids' % kind, '')

    def canonical_rows(self):
        return self.db.execute("SELECT media_id,media_type,value FROM uniqueid "
                               "WHERE type='plex' ORDER BY media_id").fetchall()

    def bucket(self, kodi_type, digits, kind='Watchlist'):
        return self.properties.get('PKC.%s.Library.%s.%d' % (kind, kodi_type, digits), '')

    def assert_native_quiet(self, kind='Watchlist'):
        self.assertFalse(self.properties.get('PKC.%s.Library.Account' % kind))

    def test_batch_matches_duplicate_editions_without_http_or_source_mutation(self):
        projection = self.membership()
        source = metadata()
        before = ET.tostring(source)
        changes = self.db.total_changes
        self.assertTrue(projection.publish('watchlist', [source], ACCOUNT))
        self.assertEqual(self.guids(), '|' + MOVIE + '|')
        self.assertEqual(self.properties['PKC.Watchlist.Account'], ACCOUNT)
        self.assertEqual(self.properties['PKC.Watchlist.Library.Account'], ACCOUNT)
        self.assertEqual(self.bucket('movie', 1), '|7|9|')
        self.assertEqual(self.db.total_changes, changes)
        self.assertEqual(self.canonical_rows(), [])
        self.assertEqual(ET.tostring(source), before)
        self.assertEqual(self.connections, 1)

    def test_same_title_without_exact_ids_never_projects_native_membership(self):
        projection = self.membership()
        source = metadata()
        source.remove(source.find('Guid'))
        projection.publish('watchlist', [source], ACCOUNT)
        self.assertEqual(self.guids(), '|' + MOVIE + '|')
        self.assertEqual(self.bucket('movie', 1), '|')
        self.assertEqual(self.canonical_rows(), [])

    def test_exact_guid_matches_support_all_video_types_and_type_collisions(self):
        projection = self.membership()
        self.synced = {('movie', 7): 101, ('tvshow', 7): 102,
                       ('season', 7): 103, ('episode', 7): 104}
        for index, media_type in enumerate(('movie', 'show', 'season', 'episode'), 101):
            self.exact['plex://%s/%s' % (media_type, KEY)] = [index]
        self.assertTrue(projection.publish('favourites', [metadata(media_type=t) for t in
                                          ('movie', 'show', 'season', 'episode')], ACCOUNT))
        for kodi_type in ('movie', 'tvshow', 'season', 'episode'):
            self.assertEqual(self.bucket(kodi_type, 1, 'Favourites'), '|7|')
        projection.publish('favourites', [metadata(media_type='episode')], ACCOUNT)
        self.assertEqual(self.bucket('movie', 1, 'Favourites'), '|')
        self.assertEqual(self.bucket('episode', 1, 'Favourites'), '|7|')
        self.assertEqual(self.canonical_rows(), [])

    def test_digit_buckets_separate_1_10_101_and_cover_signed_int32(self):
        projection = self.membership()
        ids = (1, 10, 101, 123, 456, 999999999, 1000000000, 2147483647)
        self.synced = {('movie', kodi_id): index for index, kodi_id in enumerate(ids, 100)}
        self.exact[MOVIE] = list(range(100, 108))
        self.assertTrue(projection.publish('watchlist', [metadata()], ACCOUNT))
        self.assertEqual(self.bucket('movie', 1), '|1|')
        self.assertEqual(self.bucket('movie', 2), '|10|')
        self.assertEqual(self.bucket('movie', 3), '|101|123|456|')
        self.assertEqual(self.bucket('movie', 9), '|999999999|')
        self.assertEqual(self.bucket('movie', 10), '|1000000000|2147483647|')
        for digits in range(4, 9):
            self.assertEqual(self.bucket('movie', digits), '|')
        self.assertNotIn('10', self.bucket('movie', 1))
        self.assertNotIn('101', self.bucket('movie', 2))

    def test_noncanonical_or_out_of_range_native_ids_never_enter_buckets(self):
        projection = self.membership()
        for kodi_id in (0, -1, 2147483648, '01', '+1', '1.0', True, 1.5, None):
            with self.subTest(kodi_id=kodi_id):
                self.synced = {('movie', kodi_id): 41}
                self.exact[MOVIE] = [41]
                projection.publish('watchlist', [metadata()], ACCOUNT)
                self.assertEqual(self.bucket('movie', 1), '|')
                self.assertEqual(self.bucket('movie', 10), '|')
        self.synced = {('movie', '123'): 41}
        projection.publish('watchlist', [metadata()], ACCOUNT)
        self.assertEqual(self.bucket('movie', 3), '|123|')

    def test_invalid_or_colliding_metadata_clears_unknown_state(self):
        projection = self.membership()
        for source in (metadata('123'), metadata(KEY + '0'), metadata(KEY[:-1]),
                       metadata('g' * 24), metadata(guid=MOVIE + '?x=1'),
                       metadata(guid='plex://show/' + KEY),
                       metadata(ratingKey=OTHER), metadata(KEY.upper()),
                       metadata(media_type='clip')):
            with self.subTest(source=source.attrib):
                projection.publish('watchlist', [metadata()], ACCOUNT)
                projection.publish('watchlist', [source], ACCOUNT)
                self.assertEqual(self.guids(), '')
                self.assertNotIn('PKC.Watchlist.Account', self.properties)
                self.assert_native_quiet()
        self.assertEqual(self.canonical_rows(), [])

    def test_unverified_match_guid_never_enters_native_buckets(self):
        projection = self.membership()
        self.after_match = lambda matches: matches[0].xml.set('guid', 'plex://movie/' + OTHER)
        projection.publish('watchlist', [metadata()], ACCOUNT)
        self.assertEqual(self.bucket('movie', 1), '|9|')
        self.assertEqual(self.canonical_rows(), [])

    def test_preserves_all_original_rows_primary_references_and_playback_fields(self):
        projection = self.membership()
        self.db.execute("INSERT INTO uniqueid VALUES(10,7,'movie','movie/wrong','plex')")
        before = list(self.db.iterdump())
        changes = self.db.total_changes
        projection.publish('watchlist', [metadata()], ACCOUNT)
        projection.publish('watchlist', [metadata()], ACCOUNT)
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertEqual(self.db.total_changes, changes)
        self.assertEqual(self.bucket('movie', 1), '|7|9|')

    def test_incomplete_failed_and_known_empty_sources_remain_distinct(self):
        projection = self.membership()
        for kwargs in ({'metadata': None}, {'metadata': [metadata()], 'complete': False}):
            projection.publish('watchlist', [metadata()], ACCOUNT)
            projection.publish('watchlist', account_hash=ACCOUNT, **kwargs)
            self.assertEqual(self.guids(), '')
            self.assertNotIn('PKC.Watchlist.Account', self.properties)
            self.assert_native_quiet()
        projection.publish('watchlist', [], ACCOUNT)
        self.assertEqual(self.guids(), '|')
        self.assertEqual(self.properties['PKC.Watchlist.Account'], ACCOUNT)
        self.assertEqual(self.properties['PKC.Watchlist.Library.Account'], ACCOUNT)
        self.assertEqual(self.bucket('movie', 1), '|')

    def test_exact_match_failure_keeps_authoritative_guids_and_native_quiet(self):
        projection = self.membership()
        with patch.object(self.api, 'mass_api', side_effect=RuntimeError('DB unavailable')):
            with self.assertLogs('PLEX.membership', level='WARNING'):
                self.assertTrue(projection.publish('favourites', [metadata()], ACCOUNT, keys={KEY}))
        self.assertIn(MOVIE, self.guids('Favourites'))
        self.assert_native_quiet('Favourites')

    def test_authoritative_keys_cover_unavailable_metadata_and_hydrated_subset(self):
        projection = self.membership()
        self.assertTrue(projection.publish('favourites', [metadata()], ACCOUNT, keys={KEY, OTHER}))
        self.assertIn('plex://episode/' + OTHER, self.guids('Favourites'))
        self.assertEqual(self.bucket('movie', 1, 'Favourites'), '|7|9|')
        self.assertTrue(projection.publish('favourites', None, ACCOUNT, keys={KEY, OTHER}))
        self.assertIn('plex://episode/' + OTHER, self.guids('Favourites'))
        self.assert_native_quiet('Favourites')
        projection.publish('favourites', [metadata()], ACCOUNT, complete=False, keys={KEY})
        self.assertEqual(self.guids('Favourites'), '')
        self.assert_native_quiet('Favourites')

    def test_metadata_outside_authoritative_keys_cannot_enter_buckets(self):
        projection = self.membership()
        projection.publish('favourites', [metadata()], ACCOUNT, keys={OTHER})
        self.assertNotIn(MOVIE, self.guids('Favourites'))
        self.assert_native_quiet('Favourites')

    def test_repeated_key_only_publication_preserves_valid_native_projection(self):
        projection = self.membership()
        projection.publish('favourites', [metadata()], ACCOUNT, keys={KEY})
        for _ in range(3):
            self.assertTrue(projection.publish_keys('favourites', {KEY}, ACCOUNT))
            self.assertEqual(self.properties['PKC.Favourites.Library.Account'], ACCOUNT)
            self.assertEqual(self.bucket('movie', 1, 'Favourites'), '|7|9|')
        projection.publish_keys('favourites', {KEY, OTHER}, ACCOUNT)
        self.assert_native_quiet('Favourites')
        projection.publish_keys('favourites', {KEY}, ACCOUNT)
        self.assert_native_quiet('Favourites')

    def test_key_only_publication_does_not_revive_invalid_native_projection(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        projection.invalidate_library()
        projection.publish_keys('watchlist', {MOVIE}, ACCOUNT)
        self.assert_native_quiet()

    def test_invalidation_clears_native_latches_and_queues_existing_jobs(self):
        projection = self.membership()
        for kind in ('watchlist', 'favourites'):
            projection.publish(kind, [metadata()], ACCOUNT)
        before = self.guids(), self.guids('Favourites')
        self.assertTrue(projection.invalidate_library())
        self.assert_native_quiet()
        self.assert_native_quiet('Favourites')
        self.assertEqual((self.guids(), self.guids('Favourites')), before)
        cache = self.package.discover_cache
        requests = [cache.claim_refresh(), cache.claim_refresh()]
        self.assertEqual({request['kind'] for request in requests},
                         {'watchlist', 'community_membership'})
        self.assertIsNone(cache.claim_refresh())

    def test_invalidation_during_match_cannot_confirm_stale_native_buckets(self):
        projection = self.membership()
        self.after_match = lambda matches: projection.invalidate_library()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        self.assert_native_quiet()

    def test_bucket_failure_never_confirms_partial_native_projection(self):
        projection = self.membership()
        def fail(name, value, clear):
            if name == 'PKC.Watchlist.Library.episode.10' and value is not None:
                raise RuntimeError('Property unavailable')
        self.before_window = fail
        with self.assertLogs('PLEX.membership', level='WARNING'):
            projection.publish('watchlist', [metadata()], ACCOUNT)
        self.assert_native_quiet()

    def test_native_and_publication_latches_follow_all_bucket_writes(self):
        projection = self.membership()
        self.assertTrue(projection.publish('watchlist', [metadata()], ACCOUNT))
        writes = [(name, value) for name, value, clear in self.window_writes if not clear]
        self.assertEqual(writes[-2:], [('PKC.Watchlist.Library.Account', ACCOUNT),
                                      ('PKC.Watchlist.Account', ACCOUNT)])
        bucket_names = {name for name, value in writes
                        if name.startswith('PKC.Watchlist.Library.')
                        and name != 'PKC.Watchlist.Library.Account'}
        self.assertEqual(len(bucket_names), 40)

    def test_sqlite_lock_contention_invalidates_latches_and_queues_retry(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        before = self.properties.copy()
        cache = self.package.discover_cache
        with sqlite3.connect(Path(self.profile.name) / 'membership-publication.sqlite',
                             timeout=0) as owner:
            self.addCleanup(owner.close)
            owner.execute('BEGIN EXCLUSIVE')
            self.assertFalse(projection.publish_keys('watchlist', {OTHER}, ACCOUNT))
            self.assertEqual(self.guids(), before['PKC.Watchlist.Guids'])
            self.assertEqual(self.bucket('movie', 1), '|7|9|')
            self.assertNotIn('PKC.Watchlist.Account', self.properties)
            self.assert_native_quiet()
        requests = [cache.claim_refresh(), cache.claim_refresh()]
        self.assertEqual({request['kind'] for request in requests},
                         {'watchlist', 'community_membership'})
        self.assertTrue(projection.publish_keys('watchlist', {OTHER}, ACCOUNT))
        self.assertNotIn(MOVIE, self.guids())
        self.assert_native_quiet()

    def test_live_publication_owner_cannot_lose_lock_after_former_lease_timeout(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        cache = self.package.discover_cache
        attempted = []

        def contend(name, value, clear):
            if name != 'PKC.Watchlist.Library.movie.1' or clear:
                return
            self.on_window = None
            for path in Path(self.profile.name).rglob('*'):
                if path.is_file():
                    os.utime(path, (time.time() - 120, time.time() - 120))
            with patch.object(cache, 'time', return_value=time.time() + 120):
                attempted.append(projection.publish_keys('watchlist', {OTHER}, ACCOUNT))
            self.assert_native_quiet()

        self.on_window = contend
        self.assertTrue(projection.publish('watchlist', [metadata()], ACCOUNT))
        self.assertEqual(attempted, [False])
        self.assertEqual(self.guids(), '|' + MOVIE + '|')
        self.assertEqual(self.bucket('movie', 1), '|7|9|')
        self.assertEqual(self.properties['PKC.Watchlist.Library.Account'], ACCOUNT)

    def test_sqlite_exclusive_connections_retain_ownership_until_rollback(self):
        path = Path(self.profile.name) / 'primitive.sqlite'
        owner = sqlite3.connect(path, timeout=0)
        contender = sqlite3.connect(path, timeout=0)
        self.addCleanup(owner.close)
        self.addCleanup(contender.close)
        owner.execute('BEGIN EXCLUSIVE')
        os.utime(path, (time.time() - 120, time.time() - 120))
        with self.assertRaisesRegex(sqlite3.OperationalError, 'database is locked'):
            contender.execute('BEGIN EXCLUSIVE')
        owner.rollback()
        contender.execute('BEGIN EXCLUSIVE')
        self.assertEqual(contender.execute('SELECT name FROM sqlite_master').fetchall(), [])
        contender.rollback()

    def test_lock_database_creation_failure_stays_quiet_and_queues_retry(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        path = Path(self.profile.name) / 'membership-publication.sqlite'
        path.unlink(missing_ok=True)
        path.mkdir()
        before = self.guids()
        self.assertFalse(projection.publish('watchlist', [], ACCOUNT))
        self.assertEqual(self.guids(), before)
        self.assertNotIn('PKC.Watchlist.Account', self.properties)
        self.assert_native_quiet()
        cache = self.package.discover_cache
        requests = [cache.claim_refresh(), cache.claim_refresh()]
        self.assertEqual({request['kind'] for request in requests},
                         {'watchlist', 'community_membership'})

    def test_async_generation_before_invalidation_cannot_publish_old_snapshot(self):
        projection = self.membership()
        self.assertTrue(callable(getattr(projection, 'generation', None)),
                        'The async source fence needs a public generation reader')
        for kind in ('watchlist', 'favourites'):
            for keys_only in (False, True):
                with self.subTest(kind=kind, keys_only=keys_only):
                    projection.publish(kind, [metadata()], ACCOUNT)
                    snapshot = projection.generation()
                    projection.invalidate_library()
                    before = self.properties.copy()
                    self.assertNotEqual(projection.generation(), snapshot)
                    if keys_only:
                        result = projection.publish_keys(kind, {OTHER}, ACCOUNT,
                                                         generation=snapshot)
                    else:
                        result = projection.publish(kind, [metadata(OTHER)], ACCOUNT,
                                                    generation=snapshot)
                    self.assertFalse(result)
                    self.assertEqual(self.properties, before)

    def test_supplied_generation_cannot_survive_invalidation_during_exact_match(self):
        projection = self.membership()
        self.assertTrue(callable(getattr(projection, 'generation', None)),
                        'The async source fence needs a public generation reader')
        snapshot = projection.generation()
        self.after_match = lambda matches: projection.invalidate_library()
        self.assertFalse(projection.publish('watchlist', [metadata()], ACCOUNT,
                                            generation=snapshot))
        self.assert_native_quiet()
        self.assertNotIn('PKC.Watchlist.Account', self.properties)

    def test_verified_watchlist_mutation_fences_old_complete_projection(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        snapshot = self.properties.get('PKC.Membership.Generation', '')
        self.assertTrue(projection.update_watchlist(OTHER, True, ACCOUNT))
        self.assertNotEqual(self.properties.get('PKC.Membership.Generation', ''), snapshot)
        before = self.properties.copy()
        self.assertFalse(projection.publish('watchlist', [metadata()], ACCOUNT,
                                            generation=snapshot))
        self.assertEqual(self.properties, before)
        self.assertIn('plex://movie/' + OTHER, self.guids())
        self.assert_native_quiet()

    def test_mutation_during_exact_match_rejects_complete_projection(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        self.after_match = lambda matches: projection.update_watchlist(OTHER, True, ACCOUNT)
        self.assertFalse(projection.publish('watchlist', [metadata()], ACCOUNT))
        self.assertIn('plex://movie/' + OTHER, self.guids())
        self.assert_native_quiet()

    def test_invalidation_before_and_after_every_publication_phase_stays_quiet(self):
        projection = self.membership()
        phases = [('PKC.Watchlist.Account', True), ('PKC.Watchlist.Guids', False),
                  ('PKC.Watchlist.Library.Account', True)]
        phases += [('PKC.Watchlist.Library.%s.%d' % (kind, digits), False)
                   for kind in ('movie', 'tvshow', 'season', 'episode')
                   for digits in range(1, 11)]
        phases += [('PKC.Watchlist.Library.Account', False), ('PKC.Watchlist.Account', False)]
        for before in (False, True):
            for phase, clearing in phases:
                with self.subTest(before=before, phase=phase, clearing=clearing):
                    def invalidate(name, value, clear):
                        if name != phase or clear != clearing:
                            return
                        self.on_window = self.before_window = None
                        projection.invalidate_library()
                    if before:
                        self.before_window = invalidate
                    else:
                        self.on_window = invalidate
                    self.assertFalse(projection.publish('watchlist', [metadata()], ACCOUNT))
                    self.assert_native_quiet()

    def test_key_only_publication_cannot_keep_foreign_native_latch(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        self.properties['PKC.Watchlist.Library.Account'] = SECOND
        self.assertTrue(projection.publish_keys('watchlist', {MOVIE}, ACCOUNT))
        self.assert_native_quiet()

    def test_account_change_during_match_preserves_new_account_projection(self):
        projection = self.membership()
        def switch(matches):
            self.properties['plex_token'] = 'account-two'
            projection.reset_account('account-two')
            projection.publish_keys('watchlist', {OTHER}, SECOND)
        self.after_match = switch
        projection.publish('watchlist', [metadata()], ACCOUNT)
        self.assertIn('plex://movie/' + OTHER, self.guids())
        self.assertEqual(self.properties['PKC.Watchlist.Account'], SECOND)
        self.assert_native_quiet()

    def test_account_switch_before_and_after_every_publication_phase(self):
        projection = self.membership()
        phases = [('PKC.Watchlist.Account', True), ('PKC.Watchlist.Guids', False),
                  ('PKC.Watchlist.Library.Account', True)]
        phases += [('PKC.Watchlist.Library.%s.%d' % (kind, digits), False)
                   for kind in ('movie', 'tvshow', 'season', 'episode')
                   for digits in range(1, 11)]
        phases += [('PKC.Watchlist.Library.Account', False), ('PKC.Watchlist.Account', False)]
        for before in (False, True):
            for phase, clearing in phases:
                with self.subTest(before=before, phase=phase, clearing=clearing):
                    self.properties.clear()
                    self.properties['plex_token'] = 'account-one'
                    projection.reset_account('account-one')
                    attempted = []
                    def switch(name, value, clear):
                        if name != phase or clear != clearing:
                            return
                        self.on_window = self.before_window = None
                        self.properties['plex_token'] = 'account-two'
                        projection.reset_account('account-two')
                        attempted.append(projection.publish_keys('watchlist', {OTHER}, SECOND))
                    if before:
                        self.before_window = switch
                    else:
                        self.on_window = switch
                    self.assertFalse(projection.publish('watchlist', [metadata()], ACCOUNT))
                    self.assertEqual(attempted, [False])
                    self.assertNotEqual(self.properties.get('PKC.Watchlist.Account'), ACCOUNT)
                    self.assert_native_quiet()
                    self.assertTrue(projection.publish_keys('watchlist', {OTHER}, SECOND))
                    self.assertNotIn(MOVIE, self.guids())
                    self.assertIn('plex://movie/' + OTHER, self.guids())
                    self.assertEqual(self.properties['PKC.Watchlist.Account'], SECOND)

    def test_failed_source_clear_cannot_erase_new_account_projection(self):
        projection = self.membership()
        def switch(name, value, clear):
            if name == 'PKC.Watchlist.Account' and clear:
                self.on_window = None
                self.properties['plex_token'] = 'account-two'
                projection.reset_account('account-two')
                projection.publish_keys('watchlist', {OTHER}, SECOND)
        self.on_window = switch
        projection.publish('watchlist', None, ACCOUNT)
        self.assertTrue(projection.publish_keys('watchlist', {OTHER}, SECOND))
        self.assertIn('plex://movie/' + OTHER, self.guids())
        self.assertEqual(self.properties['PKC.Watchlist.Account'], SECOND)

    def test_stale_account_cannot_clear_or_replace_current_projection(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        before = self.properties.copy()
        projection.publish('watchlist', None, SECOND)
        projection.publish_keys('watchlist', {OTHER}, SECOND)
        projection.update_watchlist(KEY, False, SECOND)
        self.assertEqual(self.properties, before)

    def test_keys_project_without_library_writes_and_invalid_keys_clear(self):
        projection = self.membership()
        projection.publish_keys('favourites', {KEY}, ACCOUNT)
        self.assertEqual(set(self.guids('Favourites').strip('|').split('|')),
                         {'plex://movie/' + KEY, 'plex://show/' + KEY,
                          'plex://season/' + KEY, 'plex://episode/' + KEY})
        self.assertEqual(self.connections, 0)
        self.assert_native_quiet('Favourites')
        with self.assertLogs('PLEX.membership', level='WARNING'):
            projection.publish_keys('favourites', {'123'}, ACCOUNT)
        self.assertEqual(self.guids('Favourites'), '')

    def test_failed_key_source_clears_projection_without_confirming_absence(self):
        projection = self.membership()
        def failed_source():
            yield KEY
            raise RuntimeError('Source incomplete')
        for source in (42, failed_source()):
            projection.publish_keys('favourites', {KEY}, ACCOUNT)
            with self.assertLogs('PLEX.membership', level='WARNING'):
                projection.publish_keys('favourites', source, ACCOUNT)
            self.assertEqual(self.guids('Favourites'), '')
            self.assertNotIn('PKC.Favourites.Account', self.properties)
            self.assert_native_quiet('Favourites')

    def test_watchlist_verified_add_remove_invalidates_native_projection(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        projection.update_watchlist(OTHER, True, ACCOUNT)
        projection.update_watchlist(KEY, False, ACCOUNT)
        self.assertEqual(self.guids(), '|plex://movie/' + OTHER + '|plex://show/' + OTHER + '|')
        self.assert_native_quiet()
        projection.update_watchlist(OTHER, False, ACCOUNT)
        self.assertEqual(self.guids(), '|')
        projection.update_watchlist('123', True, ACCOUNT)
        self.assertEqual(self.guids(), '|')

    def test_delta_cannot_turn_unknown_source_into_complete_list(self):
        projection = self.membership()
        projection.update_watchlist(KEY, True, ACCOUNT)
        self.assertNotIn('PKC.Watchlist.Account', self.properties)
        self.assertEqual(self.guids(), '')

    def test_reset_only_clears_membership_on_account_change_or_logout(self):
        projection = self.membership()
        projection.publish('watchlist', [metadata()], ACCOUNT)
        projection.publish('favourites', [metadata()], ACCOUNT)
        self.properties['PKC.Favourites.ListName'] = 'My favourites'
        projection.reset_account('account-one')
        self.assertIn(MOVIE, self.guids())
        self.assertEqual(self.properties['PKC.Watchlist.Library.Account'], ACCOUNT)
        self.properties['plex_token'] = 'account-two'
        projection.reset_account('account-two')
        self.assertEqual(self.properties['PKC.Account.Hash'], SECOND)
        for name in ('PKC.Watchlist.Guids', 'PKC.Watchlist.Account',
                     'PKC.Favourites.Guids', 'PKC.Favourites.Account',
                     'PKC.Watchlist.Library.Account', 'PKC.Favourites.Library.Account',
                     'PKC.Favourites.ListName'):
            self.assertNotIn(name, self.properties)
        projection.reset_account(None)
        self.assertNotIn('PKC.Account.Hash', self.properties)

    def test_base_guids_exposes_full_canonical_id_after_existing_providers(self):
        row = metadata(ratingKey='41')
        ET.SubElement(row, 'Guid', id='imdb://tt0133093')
        guids = self.base.Base(row).guids
        self.assertEqual(guids, {'tmdb': '603', 'imdb': 'tt0133093', 'plex': MOVIE})
        self.assertEqual(next(iter(guids)), 'tmdb')

    def test_base_guids_rejects_malformed_or_wrong_type_canonical_ids(self):
        for guid in ('plex://movie/123', MOVIE + '/extra', 'plex://show/' + KEY,
                     'plex://movie/' + KEY.upper()):
            with self.subTest(guid=guid):
                self.assertNotIn('plex', self.base.Base(metadata(guid=guid)).guids)

    def test_base_plex_child_uses_full_guid_and_rejects_invalid_child(self):
        row = metadata()
        ET.SubElement(row, 'Guid', id=MOVIE)
        self.assertEqual(self.base.Base(row).guids['plex'], MOVIE)
        row.set('guid', 'legacy://source')
        row.find("Guid[@id='%s']" % MOVIE).set('id', 'plex://movie/123')
        self.assertNotIn('plex', self.base.Base(row).guids)

    def test_account_load_clear_and_logout_manage_projection(self):
        projection = self.membership()
        account = self.load('resources.lib.app.account', 'resources/lib/app/account.py')
        self.settings['plexToken'] = 'account-one'
        with patch.object(account.Account, 'reset_session'):
            instance = account.Account()
            projection.publish_keys('watchlist', {KEY}, ACCOUNT)
            instance.load()
            self.assertIn(MOVIE, self.guids())
            self.settings['plexToken'] = 'account-two'
            instance.load()
            self.assertEqual(self.properties['PKC.Account.Hash'], SECOND)
            self.assertEqual(self.guids(), '')
            projection.publish_keys('watchlist', {OTHER}, SECOND)
            instance.clear()
            self.assertNotIn('PKC.Account.Hash', self.properties)
            self.assertEqual(self.guids(), '')
            self.settings['plexToken'] = 'account-one'
            instance.load()
            projection.publish_keys('watchlist', {KEY}, ACCOUNT)
            instance.log_out()
            self.assertNotIn('PKC.Account.Hash', self.properties)
            self.assertEqual(self.guids(), '')


if __name__ == '__main__':
    unittest.main()
