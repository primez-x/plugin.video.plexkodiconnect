import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from library_sync import isolate_modules


class ContextMenuMetadataTests(unittest.TestCase):
    def setUp(self):
        isolate_modules(self)
        package = importlib.import_module('resources.lib')
        sys.modules.pop('resources.lib.contextmenu.menu', None)
        context = types.ModuleType('resources.lib.contextmenu')
        context.__path__ = [str(Path(__file__).resolve().parents[1] /
                               'resources/lib/contextmenu')]
        sys.modules[context.__name__] = context
        self.options = []
        self.actions = []
        test = self

        class Dialog:
            def __init__(self, *args):
                pass

            def set_options(self, options):
                test.options = options

            def doModal(self):
                pass

            def is_selected(self):
                return False

        stubs = {
            'xbmc': {'executebuiltin': self.actions.append},
            'resources.lib.contextmenu.common': {'plex_id_from_listitem': lambda: 7},
            'resources.lib.windows': {'contextmenu': SimpleNamespace(ContextMenuDialog=Dialog)},
            'resources.lib.plex_api': {'API': object},
            'resources.lib.plex_functions': {'GetPlexMetadata': lambda plex_id: None},
            'resources.lib.utils': {'lang': str, 'window': lambda key: ''},
            'resources.lib.variables': {
                'PLEX_TYPE_FROM_KODI_TYPE': {'movie': 'movie'},
                'KODI_VIDEOTYPES': ('movie',), 'ADDON_PATH': ''},
            'resources.lib.app': {
                'SYNC': SimpleNamespace(direct_paths=True),
                'PLAYSTATE': SimpleNamespace(force_transcode=False, context_menu_play=False),
                'PLAYQUEUES': SimpleNamespace(from_kodi_type=lambda typ: self.fail('Queue changed'))},
        }
        for name, attributes in stubs.items():
            module = types.ModuleType(name)
            vars(module).update(attributes)
            sys.modules[name] = module
            if name.startswith('resources.lib.') and name.count('.') == 2:
                setattr(package, name.rsplit('.', 1)[1], module)
        self.module = importlib.import_module('resources.lib.contextmenu.menu')

    def test_failed_metadata_hides_playback_options(self):
        self.module.ContextMenu(kodi_id=1, kodi_type='movie')
        self.assertNotIn(self.module.OPTIONS['PMS_Play'], self.options)
        self.assertNotIn(self.module.OPTIONS['Transcode'], self.options)
        self.assertIn(self.module.OPTIONS['Analyze'], self.options)
        self.assertIn(self.module.OPTIONS['Addon'], self.options)

    def test_missing_metadata_playback_does_not_change_playstate_or_queue(self):
        menu = self.module.ContextMenu(kodi_id=1, kodi_type='movie')
        for action in ('PMS_Play', 'Transcode'):
            menu._selected_option = self.module.OPTIONS[action]
            menu._action_menu()
        self.assertFalse(self.module.app.PLAYSTATE.force_transcode)
        self.assertFalse(self.module.app.PLAYSTATE.context_menu_play)
        self.assertEqual(self.actions, [])
