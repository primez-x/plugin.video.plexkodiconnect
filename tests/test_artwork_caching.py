import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_artwork_module(rows):
    import resources.lib as package

    def stub(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        sys.modules[name] = module
        setattr(package, name.rsplit('.', 1)[1], module)
        return module

    class FakeVideoDB(object):
        queries = []

        def __init__(self, texture_db=False):
            self.kodiconn = self.artconn = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def artwork_generator(self, kodi_type, limit, offset):
            FakeVideoDB.queries.append(offset)
            # Fail instead of hanging if the generator never stops
            assert len(FakeVideoDB.queries) < 100, 'endless batch loop'
            return iter(rows[offset:offset + limit])

    class FakeTextureDB(object):
        def __init__(self, **kwargs):
            pass

        def url_not_yet_cached(self, url):
            return True

    stub('resources.lib.kodi_db', KodiVideoDB=FakeVideoDB,
         KodiMusicDB=FakeVideoDB, KodiTextureDB=FakeTextureDB)
    stub('resources.lib.app')
    stub('resources.lib.backgroundthread', KillableThread=object)
    stub('resources.lib.utils')
    spec = importlib.util.spec_from_file_location(
        'resources.lib.artwork', REPO_ROOT / 'resources' / 'lib' / 'artwork.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['resources.lib.artwork'] = module
    spec.loader.exec_module(module)
    return module, FakeVideoDB


class UrlGeneratorTests(unittest.TestCase):
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

    def _urls(self, count):
        rows = ['url%s' % i for i in range(count)]
        artwork, db = _load_artwork_module(rows)
        urls = []
        for url in artwork.ImageCachingThread._url_generator(db, 'poster'):
            urls.append(url)
        self.assertEqual(urls, rows)
        return db.queries

    def test_stops_after_empty_batch_when_total_is_multiple_of_batch(self):
        self.assertEqual(self._urls(500), [0, 500])
        self.assertEqual(self._urls(1000), [0, 500, 1000])

    def test_stops_after_short_batch(self):
        self.assertEqual(self._urls(0), [0])
        self.assertEqual(self._urls(501), [0, 500])


if __name__ == '__main__':
    unittest.main()
