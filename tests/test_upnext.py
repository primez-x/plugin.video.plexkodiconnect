import importlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_UPNEXT_SETTINGS = {
    'disableNextUp': 'false',
}


def load_upnext(pkc_enabled='true', system_enabled=True,
                service_settings=None, pkc_settings=None, addon_error=False):
    for module_name in (
            'xbmc',
            'xbmcaddon',
            'resources.lib.upnext',
            'resources.lib.utils',
            'resources.lib.variables',
            'resources.lib.plex_functions',
            'resources.lib.plex_api',
            'resources.lib.plex_db',
            'resources.lib.kodi_db'):
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType('xbmc')
    xbmc.getCondVisibility = lambda condition: system_enabled
    xbmc.executeJSONRPC = lambda payload: '{}'
    sys.modules['xbmc'] = xbmc

    settings = dict(DEFAULT_UPNEXT_SETTINGS)
    settings.update(service_settings or {})
    xbmcaddon = types.ModuleType('xbmcaddon')

    class Addon(object):
        def __init__(self, id=None):
            if addon_error:
                raise RuntimeError('service unavailable')
            self.id = id

        def getSetting(self, setting):
            return settings.get(setting, '')

    xbmcaddon.Addon = Addon
    sys.modules['xbmcaddon'] = xbmcaddon

    pkc_values = {
        'useUpNextForEpisodeCredits': pkc_enabled,
    }
    pkc_values.update(pkc_settings or {})
    utils = types.ModuleType('resources.lib.utils')
    utils.settings = lambda setting: pkc_values.get(setting, '')
    sys.modules['resources.lib.utils'] = utils

    variables = types.ModuleType('resources.lib.variables')
    variables.ADDON_ID = 'plugin.video.plexkodiconnect'
    variables.PLEX_TYPE_EPISODE = 'episode'
    sys.modules['resources.lib.variables'] = variables

    plex_functions = types.ModuleType('resources.lib.plex_functions')
    plex_functions.show_episodes = lambda show_id: None
    sys.modules['resources.lib.plex_functions'] = plex_functions

    plex_api = types.ModuleType('resources.lib.plex_api')
    plex_api.API = object
    sys.modules['resources.lib.plex_api'] = plex_api

    plex_db = types.ModuleType('resources.lib.plex_db')
    plex_db.PlexDB = object
    sys.modules['resources.lib.plex_db'] = plex_db

    kodi_db = types.ModuleType('resources.lib.kodi_db')
    kodi_db.KodiVideoDB = object
    sys.modules['resources.lib.kodi_db'] = kodi_db

    return importlib.import_module('resources.lib.upnext')


def status(total_seconds, first=None, final=None):
    whole_seconds = int(total_seconds)
    milliseconds = int(round((total_seconds - whole_seconds) * 1000))
    return {
        'totaltime': {
            'hours': whole_seconds // 3600,
            'minutes': (whole_seconds % 3600) // 60,
            'seconds': whole_seconds % 60,
            'milliseconds': milliseconds,
        },
        'first_credits_marker': first,
        'final_credits_marker': final,
    }


class UpNextCreditTimingTests(unittest.TestCase):
    def test_notification_fires_at_marker_for_every_runtime_bucket(self):
        upnext = load_upnext()
        for total_seconds in (600, 601, 1201, 2401, 3601):
            with self.subTest(total_seconds=total_seconds):
                marker = (total_seconds - 100, total_seconds, 'credits', True)
                actual = upnext.get_notification_time_from_markers(
                    status(total_seconds, final=marker))
                self.assertEqual(actual, 100)

    def test_marker_remaining_includes_milliseconds(self):
        upnext = load_upnext()

        actual = upnext.get_notification_time_from_markers(
            status(1422.050,
                   final=(1373.845, 1422.051, 'credits', True)))

        self.assertAlmostEqual(actual, 48.205, places=3)

    def test_first_credit_marker_is_preferred(self):
        upnext = load_upnext()

        actual = upnext.get_notification_time_from_markers(
            status(1000,
                   first=(800, 850, 'credits', False),
                   final=(950, 1000, 'credits', True)))

        self.assertEqual(actual, 200)

    def test_invalid_first_marker_falls_back_to_final_marker(self):
        upnext = load_upnext()

        actual = upnext.get_notification_time_from_markers(
            status(1000,
                   first=(1000, 1050, 'credits', False),
                   final=(950, 1000, 'credits', True)))

        self.assertEqual(actual, 50)

    def test_missing_marker_uses_native_upnext_timing(self):
        upnext = load_upnext()

        self.assertIsNone(upnext.get_notification_time_from_markers(
            status(1400)))

    def test_disabled_policy_uses_native_upnext_timing(self):
        for kwargs in (
                {'pkc_enabled': 'false'},
                {'system_enabled': False},
                {'service_settings': {'disableNextUp': 'true'}},
                {'addon_error': True}):
            with self.subTest(kwargs=kwargs):
                upnext = load_upnext(**kwargs)
                self.assertIsNone(upnext.get_notification_time_from_markers(
                    status(1400,
                           final=(1350, 1400, 'credits', True))))

    def test_invalid_markers_use_native_upnext_timing(self):
        upnext = load_upnext()

        self.assertIsNone(upnext.get_notification_time_from_markers(
            status(1400, first=('bad',), final=(1500, 1600))))

    def test_credit_countdown_defaults_to_ten_seconds(self):
        upnext = load_upnext()

        self.assertEqual(upnext.credit_countdown_seconds(), 10)

    def test_credit_countdown_is_configurable_and_bounded(self):
        cases = (
            ('2', 5),
            ('7', 7),
            ('120', 30),
            ('invalid', 10),
        )
        for configured, expected in cases:
            with self.subTest(configured=configured):
                upnext = load_upnext(pkc_settings={
                    'upNextEpisodeCreditsCountdown': configured,
                })
                self.assertEqual(upnext.credit_countdown_seconds(), expected)

    def test_signal_contains_marker_anchored_notification_time(self):
        upnext = load_upnext()
        captured = []

        class Api(object):
            plex_type = 'episode'
            plex_id = '1'

            def title(self):
                return 'Current'

            def grandparent_title(self):
                return 'Show'

            def season_number(self):
                return 1

            def index(self):
                return 1

        current = Api()
        next_episode = Api()
        next_episode.plex_id = '2'
        upnext._get_next_episode_api = lambda api: next_episode
        upnext._episode_info = lambda api: {'plex_id': api.plex_id}
        upnext._upnext_signal = captured.append

        handoff = upnext.send_upnext_signal(current, 88.205)
        self.assertEqual(handoff['next_plex_id'], '2')
        self.assertEqual(len(handoff['token']), 32)
        self.assertIn('pkc_upnext=%s' % handoff['token'],
                      captured[0]['play_url'])
        self.assertEqual(captured[0]['notification_time'], 88.205)
        self.assertEqual(captured[0]['notification_duration'], 10)

    def test_signal_omits_notification_time_for_native_fallback(self):
        upnext = load_upnext()
        captured = []

        class Api(object):
            plex_type = 'episode'
            plex_id = '1'

            def title(self):
                return 'Current'

            def grandparent_title(self):
                return 'Show'

            def season_number(self):
                return 1

            def index(self):
                return 1

        current = Api()
        next_episode = Api()
        next_episode.plex_id = '2'
        upnext._get_next_episode_api = lambda api: next_episode
        upnext._episode_info = lambda api: {'plex_id': api.plex_id}
        upnext._upnext_signal = captured.append

        self.assertTrue(upnext.send_upnext_signal(current, None))
        self.assertNotIn('notification_time', captured[0])
        self.assertNotIn('notification_duration', captured[0])


class FakeEpisodeAPI(object):
    """Mirrors the plex_api.API accessors the next-episode lookup uses."""
    def __init__(self, xml):
        self.xml = xml

    @staticmethod
    def _int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @property
    def plex_id(self):
        return self._int(self.xml.get('ratingKey'))

    def grandparent_id(self):
        return self._int(self.xml.get('grandparentRatingKey'))

    def season_id(self):
        return self._int(self.xml.get('parentRatingKey'))

    def season_number(self):
        return self._int(self.xml.get('parentIndex'))

    def index(self):
        return self._int(self.xml.get('index'))

    def grandparent_title(self):
        return 'Show'


def episode_xml(plex_id, season_id, season, index, show_id=100):
    return ET.Element('Video', ratingKey=str(plex_id),
                      parentRatingKey=str(season_id),
                      grandparentRatingKey=str(show_id),
                      parentIndex=str(season), index=str(index))


def container(*children):
    root = ET.Element('MediaContainer')
    root.extend(children)
    return root


# Show 100: specials season 10 (S0E1 = 1000), season 11 (S1E1-2 = 1001,
# 1002), season 12 (S2E1 = 1003). Kodi show id 7, Kodi episode ids 50+.
EPISODES = (
    (1000, 10, 0, 1),
    (1001, 11, 1, 1),
    (1002, 11, 1, 2),
    (1003, 12, 2, 1),
)


class NextEpisodeLookupTests(unittest.TestCase):
    def load(self, episodes=EPISODES, kodi_numbers=None, synced=True,
             local_episodes=None):
        upnext = load_upnext()
        upnext.API = FakeEpisodeAPI
        calls = {'metadata': [], 'children': [], 'all_leaves': 0}
        by_id = {plex_id: (season_id, season, index)
                 for plex_id, season_id, season, index in episodes}

        def metadata(plex_id):
            calls['metadata'].append(plex_id)
            season_id, season, index = by_id[int(plex_id)]
            return container(episode_xml(plex_id, season_id, season, index))

        def children(plex_id, exclude_all_leaves=False):
            calls['children'].append(plex_id)
            if plex_id == 100:
                seasons = sorted({(season, season_id) for _, season_id, season, _
                                  in episodes})
                return container(*[
                    ET.Element('Directory', ratingKey=str(season_id),
                               index=str(season))
                    for season, season_id in seasons])
            return container(*[
                episode_xml(pid, sid, season, index)
                for pid, sid, season, index in episodes if sid == plex_id])

        def all_leaves(show_id):
            calls['all_leaves'] += 1
            return container(*[episode_xml(*episode) for episode in episodes])

        upnext.PF.GetPlexMetadata = metadata
        upnext.PF.metadata_children = children
        upnext.PF.show_episodes = all_leaves

        # Episodes synced to PKC's databases; defaults to all PMS episodes
        local_episodes = episodes if local_episodes is None else local_episodes
        numbers = kodi_numbers or {
            50 + offset: (season, index)
            for offset, (_, _, season, index) in enumerate(local_episodes)}
        kodi_ids = {plex_id: 50 + offset
                    for offset, (plex_id, _, _, _)
                    in enumerate(local_episodes)}

        class FakePlexDB(object):
            def __init__(self, lock=False):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def episode(self, plex_id):
                if not synced or plex_id not in kodi_ids:
                    return None
                return {'plex_id': plex_id, 'grandparent_id': 7,
                        'kodi_id': kodi_ids[plex_id]}

            def episode_by_show(self, show_id):
                return iter([{'plex_id': plex_id, 'kodi_id': kodi_id}
                             for plex_id, kodi_id in kodi_ids.items()]
                            if synced else [])

        class FakeKodiVideoDB(FakePlexDB):
            def episode_numbers_by_show(self, kodi_show_id):
                return [(kodi_id, str(season), str(index))
                        for kodi_id, (season, index) in numbers.items()]

        upnext.PlexDB = FakePlexDB
        upnext.KodiVideoDB = FakeKodiVideoDB
        return upnext, calls

    def current(self, plex_id):
        for pid, season_id, season, index in EPISODES:
            if pid == plex_id:
                return FakeEpisodeAPI(episode_xml(pid, season_id, season, index))

    def test_local_database_answers_with_a_single_metadata_request(self):
        upnext, calls = self.load()

        api = upnext._get_next_episode_api(self.current(1002))

        self.assertEqual(api.plex_id, 1003)
        self.assertEqual(calls['metadata'], [1003])
        self.assertEqual(calls['children'], [])
        self.assertEqual(calls['all_leaves'], 0)

    def test_specials_lead_into_the_first_season(self):
        upnext, calls = self.load()

        api = upnext._get_next_episode_api(self.current(1000))

        self.assertEqual(api.plex_id, 1001)
        self.assertEqual(calls['all_leaves'], 0)

    def test_unsynced_show_uses_season_children_not_all_leaves(self):
        upnext, calls = self.load(synced=False)

        within = upnext._get_next_episode_api(self.current(1001))
        across = upnext._get_next_episode_api(self.current(1002))

        self.assertEqual(within.plex_id, 1002)
        self.assertEqual(across.plex_id, 1003)
        self.assertEqual(calls['children'], [11, 11, 100, 12])
        self.assertEqual(calls['all_leaves'], 0)

    def test_last_episode_is_confirmed_on_the_server(self):
        upnext, calls = self.load()

        self.assertIsNone(upnext._get_next_episode_api(self.current(1003)))
        self.assertEqual(calls['children'], [12, 100])
        self.assertEqual(calls['all_leaves'], 0)

    def test_disagreeing_orders_fall_back_to_all_leaves(self):
        # S1E2 was added to the library after S2E1: plex_id order and
        # season/episode order disagree, so only allLeaves is authoritative.
        episodes = (
            (1001, 11, 1, 1),
            (1005, 11, 1, 2),
            (1003, 12, 2, 1),
        )
        upnext, calls = self.load(episodes=episodes)
        current = FakeEpisodeAPI(episode_xml(1001, 11, 1, 1))

        api = upnext._get_next_episode_api(current)

        self.assertEqual(calls['all_leaves'], 1)
        self.assertEqual(api.plex_id, 1005)

    def test_unsynced_episode_within_season_is_not_skipped(self):
        # S1E2 exists on the PMS but is not (yet) synced locally
        episodes = (
            (1001, 11, 1, 1),
            (1002, 11, 1, 2),
            (1003, 11, 1, 3),
        )
        upnext, calls = self.load(
            episodes=episodes, local_episodes=(episodes[0], episodes[2]))
        current = FakeEpisodeAPI(episode_xml(1001, 11, 1, 1))

        api = upnext._get_next_episode_api(current)

        self.assertEqual(api.plex_id, 1002)
        self.assertEqual(calls['children'], [11])
        self.assertEqual(calls['all_leaves'], 0)

    def test_unsynced_first_episode_of_next_season_is_not_skipped(self):
        # S2E1 exists on the PMS but is not (yet) synced locally
        episodes = (
            (1001, 11, 1, 1),
            (1002, 12, 2, 1),
            (1003, 12, 2, 2),
        )
        upnext, calls = self.load(
            episodes=episodes, local_episodes=(episodes[0], episodes[2]))
        current = FakeEpisodeAPI(episode_xml(1001, 11, 1, 1))

        api = upnext._get_next_episode_api(current)

        self.assertEqual(api.plex_id, 1002)
        self.assertEqual(calls['children'], [11, 100, 12])
        self.assertEqual(calls['all_leaves'], 0)

    def test_specials_are_not_answered_from_the_local_database(self):
        upnext, calls = self.load()

        result = upnext._next_episode_from_local_db(self.current(1000))

        self.assertIs(result, upnext._UNKNOWN)
        self.assertEqual(calls['metadata'], [])

    def test_contiguity_rule(self):
        upnext = load_upnext()

        self.assertTrue(upnext._is_contiguous((1, 4), (1, 5)))
        self.assertTrue(upnext._is_contiguous((1, 9), (2, 1)))
        self.assertFalse(upnext._is_contiguous((1, 4), (1, 6)))
        self.assertFalse(upnext._is_contiguous((1, 9), (2, 2)))
        self.assertFalse(upnext._is_contiguous((1, 9), (3, 1)))
        self.assertFalse(upnext._is_contiguous((0, 1), (1, 1)))
        self.assertFalse(upnext._is_contiguous((None, 1), (1, 2)))
        self.assertFalse(upnext._is_contiguous((1, None), (1, 2)))


if __name__ == '__main__':
    unittest.main()
