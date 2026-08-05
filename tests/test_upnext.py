import importlib
import sys
import types
import unittest
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
            'resources.lib.plex_api'):
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


if __name__ == '__main__':
    unittest.main()
