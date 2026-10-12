import unittest
from types import SimpleNamespace

from . import isolate_modules, load_library_sync_module


class UpdateKodiLibraryTests(unittest.TestCase):
    def setUp(self):
        isolate_modules(self)
        self.conditions = {'Window.IsMedia': True, 'Container.IsUpdating': True}
        self.builtins = []
        self.app = SimpleNamespace(APP=SimpleNamespace(
            update_widgets=False, refresh_container=False))
        self.common = load_library_sync_module('common', {
            'xbmc': {
                'getCondVisibility': lambda condition: self.conditions[condition],
                'executebuiltin': self.builtins.append},
            'resources.lib.utils': {'settings': lambda key: ''},
            'resources.lib.app': {'APP': self.app.APP},
            'resources.lib.variables': {'DEVICE': 'Linux'},
            'resources.lib.gui_refresh': {},
        })

    def test_media_window_is_refreshed_only_after_it_finished_loading(self):
        self.common.update_kodi_library(video=True, music=False)
        self.assertEqual(self.builtins, [])
        self.assertTrue(self.app.APP.update_widgets)

        # Service loop: still loading
        self.common.refresh_container_if_pending()
        self.assertEqual(self.builtins, [])

        self.conditions['Container.IsUpdating'] = False
        self.common.refresh_container_if_pending()
        self.common.refresh_container_if_pending()
        self.assertEqual(self.builtins, ['Container.Refresh'])

    def test_loaded_media_window_is_refreshed_right_away(self):
        self.conditions['Container.IsUpdating'] = False
        self.common.update_kodi_library(video=True, music=False)
        self.assertEqual(self.builtins, ['Container.Refresh'])

    def test_pending_refresh_is_dropped_after_leaving_the_media_window(self):
        self.common.update_kodi_library(video=True, music=False)
        self.conditions.update({'Window.IsMedia': False,
                                'Container.IsUpdating': False})
        self.common.refresh_container_if_pending()
        self.assertEqual(self.builtins, [])
        # The widget reload on the home screen still happens
        self.assertTrue(self.app.APP.update_widgets)


if __name__ == '__main__':
    unittest.main()
