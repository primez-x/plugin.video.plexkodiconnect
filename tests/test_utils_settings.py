import importlib
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Simulates PKC's settings.xml on disk
STORE = {}
INSTANCES = []


class FakeAddon(object):
    """Like Kodi's xbmcaddon.Addon: settings are loaded at construction."""

    def __init__(self, id=None):
        INSTANCES.append(self)
        self.values = dict(STORE)

    def getSetting(self, key):
        return self.values.get(key, '')

    def setSetting(self, key, value):
        self.values[key] = value
        STORE[key] = value

    def getLocalizedString(self, string_id):
        return ''


def load_utils():
    modules = {}
    for name in ('xbmc', 'xbmcaddon', 'xbmcgui'):
        modules[name] = types.ModuleType(name)
    modules['xbmcaddon'].Addon = FakeAddon

    class Window(object):
        def __init__(self, window_id=None):
            pass

    modules['xbmcgui'].Window = Window
    for name in ('pathvalidate', 'path_ops', 'variables'):
        modules['resources.lib.%s' % name] = types.ModuleType(
            'resources.lib.%s' % name)
    sys.modules.update(modules)
    sys.modules.pop('resources.lib.utils', None)
    package = importlib.import_module('resources.lib')
    for name in ('pathvalidate', 'path_ops', 'variables', 'utils'):
        package.__dict__.pop(name, None)
    return importlib.import_module('resources.lib.utils')


class SettingsTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        package = importlib.import_module('resources.lib')
        saved = dict(vars(package))

        def restore():
            for name in list(vars(package)):
                if name not in saved:
                    delattr(package, name)
            for name, value in saved.items():
                setattr(package, name, value)
        self.addCleanup(restore)
        STORE.clear()
        STORE.update({'a': '1', 'b': '2'})
        self.utils = load_utils()
        del INSTANCES[:]

    def test_settings_batch_shares_one_addon_instance(self):
        utils = self.utils
        with utils.settings_batch():
            self.assertEqual(utils.settings('a'), '1')
            self.assertEqual(utils.settings('b'), '2')
            with utils.settings_batch():
                utils.settings('c', value='3')
                # The shared instance sees its own writes
                self.assertEqual(utils.settings('c'), '3')
        self.assertEqual(len(INSTANCES), 1)
        self.assertEqual(STORE['c'], '3')

        # Outside a batch every call reads the current settings again
        STORE['a'] = 'changed elsewhere'
        self.assertEqual(utils.settings('a'), 'changed elsewhere')
        self.assertEqual(len(INSTANCES), 2)

    def test_settings_batch_is_per_thread(self):
        utils = self.utils
        seen = []
        with utils.settings_batch():
            utils.settings('a')
            STORE['a'] = 'new'
            thread = threading.Thread(
                target=lambda: seen.append(utils.settings('a')))
            thread.start()
            thread.join()
            self.assertEqual(utils.settings('a'), '1')
        self.assertEqual(seen, ['new'])

    def test_cached_setting_reads_once_until_settings_change(self):
        utils = self.utils
        self.assertEqual(utils.cached_setting('a'), '1')
        STORE['a'] = 'changed in the settings dialog'
        for _ in range(10):
            self.assertEqual(utils.cached_setting('a'), '1')
        self.assertEqual(len(INSTANCES), 1)

        # KodiMonitor.onSettingsChanged()
        utils.invalidate_cached_settings()
        self.assertEqual(
            utils.cached_setting('a'), 'changed in the settings dialog')
        self.assertEqual(len(INSTANCES), 2)

    def test_pkc_writes_invalidate_cached_settings(self):
        utils = self.utils
        self.assertEqual(utils.cached_setting('a'), '1')
        utils.settings('a', value='written by PKC')
        self.assertEqual(utils.cached_setting('a'), 'written by PKC')


if __name__ == '__main__':
    unittest.main()
