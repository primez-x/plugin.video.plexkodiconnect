import importlib.util
import os
import sys
import tempfile
import threading
import types
import unittest
import xml.etree.ElementTree as etree
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_media_module(temp_dir):
    for module_name in (
        'resources.lib.app',
        'resources.lib.clientinfo',
        'resources.lib.downloadutils',
        'resources.lib.path_ops',
        'resources.lib.plex_api',
        'resources.lib.plex_api.media',
        'resources.lib.plex_functions',
        'resources.lib.utils',
        'resources.lib.variables',
    ):
        sys.modules.pop(module_name, None)

    def stub(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        sys.modules[name] = module
        return module

    def create_unique_path(directory, filename, extension):
        path = os.path.join(directory, '%s.%s' % (filename, extension))
        number = 0
        while os.path.exists(path):
            number += 1
            path = os.path.join(
                directory, '%s_%02d.%s' % (filename, number, extension))
        return path

    stub('resources.lib.app', CONN=SimpleNamespace(server='http://pms:32400'))
    stub('resources.lib.clientinfo')
    stub('resources.lib.downloadutils', DownloadUtils=object)
    stub('resources.lib.path_ops', create_unique_path=create_unique_path)
    stub('resources.lib.plex_functions')
    stub('resources.lib.utils', cast=lambda func, value: func(value))
    stub('resources.lib.variables', EXTERNAL_SUBTITLE_TEMP_PATH=temp_dir)
    plex_api = stub('resources.lib.plex_api')
    plex_api.__path__ = [str(REPO_ROOT / 'resources' / 'lib' / 'plex_api')]

    module_path = REPO_ROOT / 'resources' / 'lib' / 'plex_api' / 'media.py'
    spec = importlib.util.spec_from_file_location(
        'resources.lib.plex_api.media', module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['resources.lib.plex_api.media'] = module
    spec.loader.exec_module(module)
    return module


VIDEO_XML = '''
<Video>
  <Media>
    <Part>
      <Stream streamType="1" codec="h264"/>
      <Stream streamType="3" key="/library/streams/1" codec="srt"
              displayTitle="English (SRT External)"/>
      <Stream streamType="3" codec="pgs" displayTitle="Embedded"/>
      <Stream streamType="3" key="/library/streams/2" codec="srt"
              displayTitle="English (SRT External)"/>
      <Stream streamType="3" key="/library/streams/3" codec="ass"
              displayTitle="Deutsch (ASS External)"/>
      <Stream streamType="3" key="/library/streams/4" codec="srt"
              displayTitle="Broken (SRT External)"/>
    </Part>
  </Media>
</Video>
'''


class ExternalSubtitleTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        import resources.lib as package
        saved = dict(vars(package))

        def restore():
            for name in list(vars(package)):
                if name not in saved:
                    delattr(package, name)
            for name, value in saved.items():
                setattr(package, name, value)
        self.addCleanup(restore)
        for name in ('app', 'clientinfo', 'downloadutils', 'path_ops',
                     'plex_api', 'plex_functions', 'utils', 'variables'):
            package.__dict__.pop(name, None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.media = _load_media_module(self.temp_dir.name)

    def _api(self):
        api = self.media.Media()
        api.xml = etree.fromstring(VIDEO_XML)
        api.part = 0
        return api

    def test_external_subtitles_download_in_parallel_and_keep_pms_order(self):
        # Every working download blocks until the other two run as well: a
        # sequential implementation breaks the barrier and loses subtitles
        barrier = threading.Barrier(3, timeout=5)
        requested = []

        class Response(object):
            def __init__(self, url):
                self.ok = not url.endswith('/4')
                self.status_code = 200 if self.ok else 404
                self.text = ''
                self.content = url.encode('utf-8')

        class FakeDownloadUtils(object):
            def downloadUrl(self, url, return_response=False):
                requested.append(url)
                if not url.endswith('/4'):
                    barrier.wait()
                return Response(url)

        self.media.DU = FakeDownloadUtils

        paths = self._api().cache_external_subs()

        self.assertEqual(sorted(requested), [
            '{server}/library/streams/%s' % i for i in (1, 2, 3, 4)])
        self.assertEqual([os.path.basename(path) for path in paths], [
            'English (SRT External).srt',
            'English (SRT External)_01.srt',
            'Deutsch (ASS External).ass',
        ])
        contents = []
        for path in paths:
            with open(path, 'rb') as subtitle:
                contents.append(subtitle.read())
        self.assertEqual(contents, [
            b'{server}/library/streams/1',
            b'{server}/library/streams/2',
            b'{server}/library/streams/3',
        ])

    def test_unreachable_pms_skips_the_subtitle_instead_of_failing(self):
        class FakeDownloadUtils(object):
            def downloadUrl(self, url, return_response=False):
                return None

        self.media.DU = FakeDownloadUtils

        self.assertEqual(self._api().cache_external_subs(), [])


if __name__ == '__main__':
    unittest.main()
