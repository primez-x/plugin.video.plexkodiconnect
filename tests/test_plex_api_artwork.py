import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, parse_qsl, quote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _install_artwork_import_stubs():
    for module_name in (
        'resources.lib.app',
        'resources.lib.artwork_urls',
        'resources.lib.downloadutils',
        'resources.lib.kodi_db',
        'resources.lib.plex_api',
        'resources.lib.plex_api.artwork',
        'resources.lib.plex_api.fanart_lookup',
        'resources.lib.utils',
        'resources.lib.variables',
    ):
        sys.modules.pop(module_name, None)

    app = types.ModuleType('resources.lib.app')
    app.CONN = SimpleNamespace(server='https://192-168-0-10.example.plex.direct:32400')
    sys.modules['resources.lib.app'] = app

    artwork_urls = types.ModuleType('resources.lib.artwork_urls')
    artwork_urls.normalize_plex_artwork_url = lambda url: url
    sys.modules['resources.lib.artwork_urls'] = artwork_urls

    downloadutils = types.ModuleType('resources.lib.downloadutils')
    downloadutils.DownloadUtils = object
    sys.modules['resources.lib.downloadutils'] = downloadutils

    kodi_db = types.ModuleType('resources.lib.kodi_db')
    kodi_db.KodiVideoDB = object
    kodi_db.KodiMusicDB = object
    sys.modules['resources.lib.kodi_db'] = kodi_db

    fanart_lookup = types.ModuleType('resources.lib.plex_api.fanart_lookup')
    sys.modules['resources.lib.plex_api.fanart_lookup'] = fanart_lookup

    utils = types.ModuleType('resources.lib.utils')
    utils.parse_qsl = parse_qsl
    utils.quote = quote
    sys.modules['resources.lib.utils'] = utils

    variables = types.ModuleType('resources.lib.variables')
    variables.PLEX_TYPE_ALBUM = 'album'
    variables.PLEX_TYPE_ARTIST = 'artist'
    variables.PLEX_TYPE_CLIP = 'clip'
    variables.PLEX_TYPE_EPISODE = 'episode'
    variables.PLEX_TYPE_MUSICVIDEO = 'musicvideo'
    variables.PLEX_TYPE_PLAYLIST = 'playlist'
    variables.PLEX_TYPE_SONG = 'track'
    variables.PLEX_TYPE_VIDEO = 'video'
    sys.modules['resources.lib.variables'] = variables

    plex_api = types.ModuleType('resources.lib.plex_api')
    plex_api.__path__ = [str(REPO_ROOT / 'resources' / 'lib' / 'plex_api')]
    sys.modules['resources.lib.plex_api'] = plex_api


def _load_artwork_module():
    module_path = REPO_ROOT / 'resources' / 'lib' / 'plex_api' / 'artwork.py'
    spec = importlib.util.spec_from_file_location('resources.lib.plex_api.artwork', module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['resources.lib.plex_api.artwork'] = module
    spec.loader.exec_module(module)
    return module


class FakeXml(dict):
    pass


class PlexArtworkUrlGenerationTests(unittest.TestCase):
    def setUp(self):
        _install_artwork_import_stubs()
        self.artwork_module = _load_artwork_module()

    def _artwork_url(self, plex_type, art_kind):
        api = self.artwork_module.Artwork()
        api.plex_type = plex_type
        api.xml = FakeXml({
            art_kind: '/library/metadata/4488/%s/1779675951' % art_kind,
        })
        api.attach_plex_token_to_url = lambda url: url + '&X-Plex-Token=token'
        return api.one_artwork(art_kind)

    def _query(self, url):
        return parse_qs(urlsplit(url).query)

    def test_fanart_uses_landscape_transcode_dimensions(self):
        query = self._query(self._artwork_url('movie', 'art'))

        self.assertEqual(query['width'], ['1280'])
        self.assertEqual(query['height'], ['720'])
        self.assertNotEqual((query['width'], query['height']), (['1920'], ['1920']))

    def test_video_thumbnail_uses_landscape_transcode_dimensions(self):
        query = self._query(self._artwork_url('episode', 'thumb'))

        self.assertEqual(query['width'], ['1280'])
        self.assertEqual(query['height'], ['720'])
        self.assertNotEqual((query['width'], query['height']), (['1920'], ['1920']))

    def test_poster_thumbnail_uses_poster_transcode_dimensions(self):
        query = self._query(self._artwork_url('movie', 'thumb'))

        self.assertEqual(query['width'], ['1000'])
        self.assertEqual(query['height'], ['1500'])
        self.assertNotEqual((query['width'], query['height']), (['1920'], ['1920']))


if __name__ == '__main__':
    unittest.main()
