import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_playback_decision():
    for module_name in (
        'resources.lib.playback_decision',
        'resources.lib.downloadutils',
        'resources.lib.plex_api',
        'resources.lib.plex_functions',
        'resources.lib.utils',
        'resources.lib.variables',
    ):
        sys.modules.pop(module_name, None)

    downloadutils = types.ModuleType('resources.lib.downloadutils')
    downloadutils.DownloadUtils = object

    plex_api = types.ModuleType('resources.lib.plex_api')
    plex_api.API = object

    plex_functions = types.ModuleType('resources.lib.plex_functions')

    utils = types.ModuleType('resources.lib.utils')
    utils.ERROR = lambda **kwargs: None
    utils.settings = lambda key: '0'
    utils.dialog = lambda *args, **kwargs: None
    utils.messageDialog = lambda *args, **kwargs: None
    utils.lang = lambda value: str(value)

    variables = types.ModuleType('resources.lib.variables')
    variables.PLAYBACK_METHOD_DIRECT_PATH = 0
    variables.PLAYBACK_METHOD_DIRECT_PLAY = 1
    variables.PLAYBACK_METHOD_DIRECT_STREAM = 2
    variables.PLAYBACK_METHOD_TRANSCODE = 3
    variables.EXPLICIT_PLAYBACK_METHOD = {
        variables.PLAYBACK_METHOD_DIRECT_PATH: 'DirectPath',
        variables.PLAYBACK_METHOD_DIRECT_PLAY: 'DirectPlay',
        variables.PLAYBACK_METHOD_DIRECT_STREAM: 'DirectStream',
        variables.PLAYBACK_METHOD_TRANSCODE: 'Transcode',
    }
    variables.PLEX_TYPE_CLIP = 'clip'
    variables.PLEX_TYPE_SONG = 'song'
    variables.PLEX_VIDEOTYPES = ('movie', 'episode', 'clip')

    sys.modules['resources.lib.downloadutils'] = downloadutils
    sys.modules['resources.lib.plex_api'] = plex_api
    sys.modules['resources.lib.plex_functions'] = plex_functions
    sys.modules['resources.lib.utils'] = utils
    sys.modules['resources.lib.variables'] = variables
    return importlib.import_module('resources.lib.playback_decision')


class PlaybackDecisionTests(unittest.TestCase):
    def test_set_playurl_preflights_http_directplay_media_part(self):
        playback_decision = load_playback_decision()
        playurl = (
            'https://192-168-0-10.plex.direct:32400/library/parts/23630/'
            '1779159316/?X-Plex-Token=token'
        )
        calls = []
        closed = []

        class FakeResponse(object):
            status_code = 206

            def close(self):
                closed.append(True)

        class FakeDownloadUtils(object):
            def downloadUrl(self, *args, **kwargs):
                calls.append((args, kwargs))
                return FakeResponse()

        playback_decision.DU = FakeDownloadUtils
        playback_decision._pms_playback_decision = lambda api, item: None
        api = SimpleNamespace(
            transcode_video_path=lambda playmethod, quality=None: playurl,
        )
        item = SimpleNamespace(
            file=None,
            playmethod=playback_decision.v.PLAYBACK_METHOD_DIRECT_PLAY,
            quality=None,
        )

        playback_decision.set_playurl(api, item)

        self.assertEqual(item.file, playurl)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], (playurl,))
        self.assertEqual(calls[0][1]['headerOptions'], {'Range': 'bytes=0-0'})
        self.assertTrue(calls[0][1]['return_response'])
        self.assertEqual(calls[0][1]['timeout'], 10)
        self.assertEqual(closed, [True])


if __name__ == '__main__':
    unittest.main()
