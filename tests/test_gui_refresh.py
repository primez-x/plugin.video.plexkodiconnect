import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_gui_refresh(labels, episodes=None, episode_error=None):
    for module_name in (
            'xbmc',
            'resources.lib.gui_refresh',
            'resources.lib.plex_db',
            'resources.lib.variables'):
        sys.modules.pop(module_name, None)
    lib_package = sys.modules.get('resources.lib')
    if lib_package is not None:
        for name in ('gui_refresh', 'plex_db', 'variables'):
            lib_package.__dict__.pop(name, None)

    xbmc = types.ModuleType('xbmc')
    xbmc.commands = []
    xbmc.getInfoLabel = lambda label: labels.get(label, '')
    xbmc.executebuiltin = xbmc.commands.append

    plex_db = types.ModuleType('resources.lib.plex_db')
    plex_db.episode_calls = []
    plex_db.open_count = 0
    episodes = {} if episodes is None else episodes

    class FakePlexDB(object):
        def __init__(self, lock=False):
            plex_db.open_count += 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def episode(self, plex_id):
            plex_db.episode_calls.append(plex_id)
            if episode_error is not None:
                raise episode_error
            return episodes.get(plex_id)

    plex_db.PlexDB = FakePlexDB
    variables = types.ModuleType('resources.lib.variables')
    variables.PLEX_TYPE_EPISODE = 'episode'
    sys.modules['xbmc'] = xbmc
    sys.modules['resources.lib.plex_db'] = plex_db
    sys.modules['resources.lib.variables'] = variables

    gui_refresh = importlib.import_module('resources.lib.gui_refresh')
    return gui_refresh, xbmc, plex_db


class GuiRefreshTests(unittest.TestCase):
    def test_refreshes_only_affected_show_for_titles_and_genres_paths(self):
        gui_refresh, xbmc, plex_db = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)':
                '530|bad|531|532|533',
            'Container(530).FolderPath':
                'videodb://tvshows/titles/160/?season=1',
            'Container(531).FolderPath':
                'videodb://tvshows/genres/22/160/?season=1',
            'Container(532).FolderPath':
                'videodb://tvshows/genres/22/999/?season=1',
            'Container(533).FolderPath':
                'videodb://movies/titles/160/',
        }, {'16388': {'grandparent_id': 160}})
        gui_refresh.uuid4 = lambda: SimpleNamespace(hex='unique-token')

        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))

        self.assertEqual(xbmc.commands, [
            'SetProperty(PKC.SyncToken.530,unique-token,10000)',
            'SetProperty(PKC.SyncToken.531,unique-token,10000)',
        ])
        self.assertEqual(plex_db.episode_calls, ['16388'])
        self.assertEqual(plex_db.open_count, 1)

    def test_unparseable_tv_path_refreshes_fail_open(self):
        gui_refresh, xbmc, _ = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)': '530',
            'Container(530).FolderPath':
                'videodb://tvshows/studios/ABC/160/?season=1',
        }, {'16388': {'grandparent_id': 999}})
        gui_refresh.uuid4 = lambda: SimpleNamespace(hex='fail-open')

        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))

        self.assertEqual(xbmc.commands, [
            'SetProperty(PKC.SyncToken.530,fail-open,10000)',
        ])

    def test_successive_invalidations_use_distinct_tokens(self):
        gui_refresh, xbmc, _ = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)': '530',
            'Container(530).FolderPath':
                'videodb://tvshows/titles/160/',
        }, {'16388': {'grandparent_id': 160}})
        tokens = iter(('token-one', 'token-two'))
        gui_refresh.uuid4 = lambda: SimpleNamespace(hex=next(tokens))

        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))
        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))

        self.assertEqual(xbmc.commands, [
            'SetProperty(PKC.SyncToken.530,token-one,10000)',
            'SetProperty(PKC.SyncToken.530,token-two,10000)',
        ])

    def test_non_episode_change_does_not_open_plex_db_or_signal_panels(self):
        gui_refresh, xbmc, plex_db = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)': '530',
            'Container(530).FolderPath':
                'videodb://tvshows/titles/160/',
        })

        gui_refresh.refresh_sidepanel_containers((('500', 'movie'),))

        self.assertEqual(xbmc.commands, [])
        self.assertEqual(plex_db.open_count, 0)

    def test_lookup_failure_refreshes_declared_tv_containers_without_raising(self):
        gui_refresh, xbmc, _ = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)': '530|531|532',
            'Container(530).FolderPath':
                'videodb://tvshows/titles/160/',
            'Container(531).FolderPath':
                'videodb://tvshows/genres/22/999/',
            'Container(532).FolderPath':
                'videodb://movies/titles/160/',
        }, episode_error=RuntimeError('database unavailable'))
        gui_refresh.LOG.exception = lambda *args, **kwargs: None
        gui_refresh.uuid4 = lambda: SimpleNamespace(hex='db-fail-open')

        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))

        self.assertEqual(xbmc.commands, [
            'SetProperty(PKC.SyncToken.530,db-fail-open,10000)',
            'SetProperty(PKC.SyncToken.531,db-fail-open,10000)',
        ])


if __name__ == '__main__':
    unittest.main()
