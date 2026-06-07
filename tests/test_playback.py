import importlib
import sys
import types
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_playback():
    for module_name in (
        'xbmc',
        'resources.lib.playback',
        'resources.lib.plex_api',
        'resources.lib.plex_db',
        'resources.lib.kodi_db',
        'resources.lib.plex_functions',
        'resources.lib.playlist_func',
        'resources.lib.json_rpc',
        'resources.lib.variables',
        'resources.lib.utils',
        'resources.lib.transfer',
        'resources.lib.playback_decision',
        'resources.lib.app',
        'resources.lib.exceptions',
    ):
        sys.modules.pop(module_name, None)

    xbmc = types.ModuleType('xbmc')
    xbmc.commands = []
    xbmc.getCondVisibility = lambda condition: False
    xbmc.executebuiltin = lambda command: xbmc.commands.append(command)
    sys.modules['xbmc'] = xbmc

    plex_api = types.ModuleType('resources.lib.plex_api')
    plex_api.API = object

    plex_db = types.ModuleType('resources.lib.plex_db')
    plex_db.PlexDB = object

    kodi_db = types.ModuleType('resources.lib.kodi_db')
    kodi_db.KodiVideoDB = object

    variables = types.ModuleType('resources.lib.variables')
    variables.PLEX_TYPE_MOVIE = 'movie'
    variables.PLEX_TYPE_EPISODE = 'episode'
    variables.PLEX_TYPE_VIDEO = 'video'
    variables.PLEX_VIDEOTYPES = ('movie', 'episode', 'clip')
    variables.PLAYBACK_METHOD_DIRECT_PATH = 0
    variables.PLAYBACK_METHOD_DIRECT_PLAY = 1
    variables.PLAYBACK_METHOD_DIRECT_STREAM = 2
    variables.PLAYBACK_METHOD_TRANSCODE = 3

    utils = types.ModuleType('resources.lib.utils')
    utils.extend_url = lambda url, params: (
        url + ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    )
    utils.settings = lambda key: 'false'
    utils.lang = lambda value: str(value)

    app = types.ModuleType('resources.lib.app')
    app.PLAYSTATE = SimpleNamespace(
        context_menu_play=False,
        force_transcode=False,
        item=None,
    )
    app.APP = SimpleNamespace(
        player=SimpleNamespace(play=lambda *args, **kwargs: None,
                               getTime=lambda: 0),
        monitor=SimpleNamespace(waitForAbort=lambda timeout: False),
        is_playing=False,
    )

    transfer = types.ModuleType('resources.lib.transfer')
    transfer.PKCListItem = object
    transfer.send = lambda *args, **kwargs: None
    transfer.wait_for_transfer = lambda *args, **kwargs: None

    exceptions = types.ModuleType('resources.lib.exceptions')

    class PlaylistError(Exception):
        pass

    exceptions.PlaylistError = PlaylistError

    for module_name, module in {
        'resources.lib.plex_api': plex_api,
        'resources.lib.plex_db': plex_db,
        'resources.lib.kodi_db': kodi_db,
        'resources.lib.plex_functions': types.ModuleType('resources.lib.plex_functions'),
        'resources.lib.playlist_func': types.ModuleType('resources.lib.playlist_func'),
        'resources.lib.json_rpc': types.ModuleType('resources.lib.json_rpc'),
        'resources.lib.variables': variables,
        'resources.lib.utils': utils,
        'resources.lib.transfer': transfer,
        'resources.lib.playback_decision': types.ModuleType('resources.lib.playback_decision'),
        'resources.lib.app': app,
        'resources.lib.exceptions': exceptions,
    }.items():
        sys.modules[module_name] = module

    return importlib.import_module('resources.lib.playback'), xbmc, app


class PlaybackTests(unittest.TestCase):
    def test_sync_kodi_resume_from_api_updates_native_bookmarks(self):
        playback, _, _ = load_playback()
        calls = []

        class FakePlexDB(object):
            def __init__(self, lock=True):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def item_by_id(self, plex_id, plex_type):
                calls.append(('lookup', plex_id, plex_type))
                return {'kodi_fileid': 32, 'kodi_fileid_2': 33}

        class FakeKodiVideoDB(object):
            def __init__(self, lock=True):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def set_resume(self, *args):
                calls.append(('resume', args))

        playback.PlexDB = FakePlexDB
        playback.KodiVideoDB = FakeKodiVideoDB
        api = SimpleNamespace(
            resume_point=lambda: 5653.428,
            runtime=lambda: 8178,
            viewcount=lambda: 0,
            lastplayed=lambda: None,
        )

        playback._sync_kodi_resume_from_api(6347, playback.v.PLEX_TYPE_MOVIE, api)

        self.assertEqual(calls, [
            ('lookup', 6347, playback.v.PLEX_TYPE_MOVIE),
            ('resume', (32, 5653.428, 8178, 0, None)),
            ('resume', (33, 5653.428, 8178, 0, None)),
        ])

    def test_use_kodi_db_offset_falls_back_to_plex_resume(self):
        playback, _, _ = load_playback()

        class FakePlexDB(object):
            def __init__(self, lock=True):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def item_by_id(self, plex_id, plex_type):
                return {'kodi_fileid': 32}

        class FakeKodiVideoDB(object):
            def __init__(self, lock=True):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get_resume(self, file_id):
                return None

        playback.PlexDB = FakePlexDB
        playback.KodiVideoDB = FakeKodiVideoDB

        offset = playback._use_kodi_db_offset(
            6347,
            playback.v.PLEX_TYPE_MOVIE,
            5653.428,
        )

        self.assertEqual(offset, 5653.428)

    def test_directplay_failure_retries_with_forced_transcode_and_resume(self):
        playback, xbmc, app = load_playback()
        item = SimpleNamespace(
            plex_id=6347,
            plex_type=playback.v.PLEX_TYPE_MOVIE,
            playmethod=playback.v.PLAYBACK_METHOD_DIRECT_PLAY,
            api=SimpleNamespace(
                fullpath=lambda force_addon=False: [
                    'plugin://plugin.video.plexkodiconnect.movies/'
                    '?mode=play&plex_id=6347&plex_type=movie'
                ],
            ),
        )

        self.assertTrue(playback._fallback_to_pms_transcode(
            item,
            5653.428,
            'Playback did not start',
        ))

        self.assertTrue(app.PLAYSTATE.context_menu_play)
        self.assertTrue(app.PLAYSTATE.force_transcode)
        self.assertEqual(len(xbmc.commands), 1)
        command = xbmc.commands[0]
        self.assertIn('RunPlugin(plugin://plugin.video.plexkodiconnect.movies/', command)
        self.assertIn('resume=1', command)
        self.assertIn('force_transcode=1', command)
        self.assertIn('pms_play=1', command)

    def test_threaded_playback_retries_attempted_item_when_playstate_was_cleared(self):
        playback, _, app = load_playback()
        attempted_item = SimpleNamespace(
            plex_id=10052,
            plex_type=playback.v.PLEX_TYPE_MOVIE,
            playmethod=playback.v.PLAYBACK_METHOD_DIRECT_PLAY,
        )
        calls = []

        playback.TRY_TO_SEEK_FOR = 0
        playback.js.get_player_ids = lambda: []
        playback.LOG.error = lambda *args, **kwargs: None
        playback._fallback_to_pms_transcode = lambda item, offset, reason: (
            calls.append((item, offset, reason)) or True
        )
        app.PLAYSTATE.item = None

        playback.threaded_playback(
            kodi_playlist='video playlist',
            startpos=0,
            offset=None,
            fallback_item=attempted_item,
        )

        self.assertEqual(calls, [
            (attempted_item, 0, 'Playback did not start'),
        ])


if __name__ == '__main__':
    unittest.main()
