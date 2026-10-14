import copy
import importlib
import os
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from library_sync import isolate_modules


class PlaylistDownloadTests(unittest.TestCase):
    def setUp(self):
        isolate_modules(self)
        package = importlib.import_module('resources.lib')
        for name in list(sys.modules):
            if name.startswith('resources.lib.playlists'):
                sys.modules.pop(name)
        package.__dict__.pop('playlists', None)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'video').mkdir()
        self.lock = Lock()
        self.rows = {}
        self.race = lambda: None
        self.downloads = []
        self.metadata = ET.fromstring(
            '<MediaContainer><Playlist ratingKey="7" updatedAt="20" '
            'title="Films" playlistType="video" /></MediaContainer>')
        self.items = ET.Element('MediaContainer')
        self.all_metadata = copy.deepcopy(self.metadata)
        test = self

        class API:
            def __init__(self, xml):
                self.xml = xml
                self.plex_id = int(xml.get('ratingKey'))

            def updated_at(self):
                return int(self.xml.get('updatedAt'))

            def title(self):
                return self.xml.get('title')

            def playlist_type(self):
                return self.xml.get('playlistType')

        def update(playlist, delete=False):
            self.assertTrue(self.lock.locked())
            if delete:
                self.rows.pop(playlist.plex_id, None)
            else:
                self.rows[playlist.plex_id] = copy.deepcopy(playlist)

        def get_playlist(plex_id=None, path=None):
            row = (self.rows.get(plex_id) if plex_id is not None else next(
                (row for row in self.rows.values() if row.kodi_path == path), None))
            return copy.deepcopy(row)

        def download(kind):
            self.assertFalse(self.lock.locked(), 'PMS download holds playlist lock')
            self.downloads.append(kind)
            race, self.race = self.race, lambda: None
            race()
            return {'metadata': self.metadata, 'items': self.items,
                    'all': self.all_metadata}[kind]

        stubs = {
            'resources.lib.utils': {'settings': lambda key: '',
                                   'valid_filename': lambda name: name},
            'resources.lib.path_ops': {
                'path': os.path, 'exists': os.path.exists,
                'remove': os.remove, 'walk': os.walk},
            'resources.lib.variables': {
                'PLAYLIST_PATH': str(self.root),
                'PLAYLIST_PATH_VIDEO': str(self.root / 'video'),
                'PLAYLIST_PATH_MUSIC': str(self.root / 'music'),
                'PLAYLIST_PATH_MIXED': str(self.root / 'mixed'),
                'KODI_TYPE_VIDEO_PLAYLIST': 'video',
                'KODI_TYPE_AUDIO_PLAYLIST': 'music',
                'PLEX_PLAYLIST_TYPE_AUDIO': 'audio',
                'PLEX_TYPE_PHOTO_PLAYLIST': 'photo',
                'KODI_PLAYLIST_TYPE_FROM_PLEX': {'video': 'video'},
                'M3U_ENCODING': 'utf-8'},
            'resources.lib.app': {
                'APP': SimpleNamespace(lock_playlists=self.lock, stop_pkc=False),
                'SYNC': SimpleNamespace(stop_sync=False, enable_music=True,
                    sync_specific_plex_playlists=False, sync_specific_kodi_playlists=False)},
            'resources.lib.plex_api': {'API': API},
            'resources.lib.playlists.db': {
                'get_playlist': get_playlist, 'update_playlist': update,
                'plex_playlist_ids': lambda: list(self.rows),
                'kodi_playlist_paths': lambda: [row.kodi_path for row in self.rows.values()]},
            'resources.lib.playlists.pms': {
                'metadata': lambda plex_id: download('metadata'),
                'get_playlist': lambda plex_id: download('items'),
                'all_playlists': lambda: download('all')},
            'resources.lib.playlists.plex_pl': {
                'IGNORE_PLEX_PLAYLIST_CHANGE': [],
                'create': lambda playlist: test.fail('Unexpected PMS mutation'),
                'delete': lambda playlist: test.fail('Unexpected PMS mutation')},
        }
        for name, attributes in stubs.items():
            module = types.ModuleType(name)
            vars(module).update(attributes)
            sys.modules[name] = module
            if name.count('.') == 2:
                setattr(package, name.rsplit('.', 1)[1], module)
        self.module = importlib.import_module('resources.lib.playlists')

    def seed(self, plex_id=7):
        playlist = self.module.Playlist()
        playlist.plex_id = plex_id
        playlist.plex_updatedat = 10
        playlist.plex_name = 'Films'
        playlist.kodi_path = str(self.root / 'video' / ('Films%s.m3u' % plex_id))
        Path(playlist.kodi_path).write_text('#original\n')
        playlist.kodi_hash = self.module.kodi_playlist_hash(playlist.kodi_path)
        self.rows[plex_id] = copy.deepcopy(playlist)
        return playlist

    def test_websocket_downloads_without_lock_then_creates_playlist(self):
        self.module.websocket(7, 5)
        self.assertEqual(self.rows[7].plex_updatedat, 20)
        self.assertEqual(Path(self.rows[7].kodi_path).read_text(), '#EXTCPlayListM3U::M3U\n\n')
        self.assertEqual(self.downloads, ['metadata', 'items'])

    def test_full_sync_downloads_without_lock_then_updates_playlist(self):
        self.seed()
        self.assertTrue(self.module.full_sync())
        self.assertEqual(self.rows[7].plex_updatedat, 20)
        self.assertEqual(self.downloads, ['all', 'items'])

    def test_concurrent_state_change_prevents_stale_replacement(self):
        for route in ('websocket', 'full'):
            with self.subTest(route=route):
                previous = self.seed()
                self.race = lambda: setattr(self.rows[7], 'plex_updatedat', 30)
                if route == 'websocket':
                    self.module.websocket(7, 5)
                else:
                    self.module.full_sync()
                self.assertEqual(self.rows[7].plex_updatedat, 30)
                self.assertEqual(Path(previous.kodi_path).read_text(), '#original\n')

    def test_local_file_change_during_download_survives(self):
        previous = self.seed()
        self.race = lambda: Path(previous.kodi_path).write_text('#user edit, pending event\n')
        self.module.websocket(7, 5)
        self.assertEqual(self.rows[7].plex_updatedat, 10)
        self.assertEqual(Path(previous.kodi_path).read_text(), '#user edit, pending event\n')

    def test_concurrent_deletion_does_not_recreate_playlist(self):
        previous = self.seed()

        def delete():
            self.rows.pop(7)
            Path(previous.kodi_path).unlink()

        self.race = delete
        self.module.websocket(7, 5)
        self.assertEqual(self.rows, {})
        self.assertEqual(list((self.root / 'video').iterdir()), [])

    def test_full_sync_retains_playlist_created_during_list_download(self):
        self.race = lambda: self.seed(8)
        self.assertTrue(self.module.full_sync())
        self.assertIn(8, self.rows)
        self.assertTrue(Path(self.rows[8].kodi_path).exists())

    def test_failed_items_download_preserves_previous_playlist(self):
        previous = self.seed()
        self.items = None
        self.module.websocket(7, 5)
        self.assertEqual(self.rows[7], previous)
        self.assertEqual(Path(previous.kodi_path).read_text(), '#original\n')

    def test_unchanged_playlist_needs_no_items_download(self):
        self.seed()
        self.rows[7].plex_updatedat = 20
        self.module.websocket(7, 5)
        self.assertEqual(self.downloads, ['metadata'])

    def test_deletion_event_needs_no_download(self):
        previous = self.seed()
        self.module.websocket(7, 9)
        self.assertEqual(self.downloads, [])
        self.assertEqual(self.rows, {})
        self.assertFalse(Path(previous.kodi_path).exists())
