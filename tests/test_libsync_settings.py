"""app.Sync reads boolean settings the way Kodi stores them ('true'/'false')"""
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Numeric settings Sync() converts with int()
NUMBERS = {'syncThreadNumber': '10', 'fullSyncInterval': '60',
           'backgroundsync_saftyMargin': '60'}


def restore_attributes(module, attributes):
    for name in list(vars(module)):
        if name not in attributes:
            delattr(module, name)
    for name, value in attributes.items():
        setattr(module, name, value)


class SyncSettingsTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        package = importlib.import_module('resources.lib')
        self.addCleanup(restore_attributes, package, dict(vars(package)))
        self.package = package

    def load_sync(self, settings):
        utils = types.ModuleType('resources.lib.utils')
        utils.settings = lambda key: settings.get(key, NUMBERS.get(key, ''))
        sys.modules['resources.lib.utils'] = utils
        self.package.utils = utils
        sys.modules.pop('resources.lib.app.libsync', None)
        app = types.ModuleType('resources.lib.app')
        app.__path__ = [str(REPO_ROOT / 'resources' / 'lib' / 'app')]
        sys.modules['resources.lib.app'] = app
        libsync = importlib.import_module('resources.lib.app.libsync')
        return libsync.Sync()

    def test_check_media_file_existence_follows_the_boolean_setting(self):
        sync = self.load_sync({'check_media_file_existence': 'true'})
        self.assertIs(sync.check_media_file_existence, True)

        sync = self.load_sync({'check_media_file_existence': 'false'})
        self.assertIs(sync.check_media_file_existence, False)


if __name__ == '__main__':
    unittest.main()
