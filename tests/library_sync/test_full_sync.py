import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from . import fakes, isolate_modules, load_library_sync_module


def videos(titles):
    for number, title in enumerate(titles):
        yield ET.Element('Video', title=title, ratingKey=str(number))


def failing_videos():
    yield from videos('ab')
    raise RuntimeError('Could not download everything')


class FullSyncPlaystateLockTests(unittest.TestCase):
    def setUp(self):
        isolate_modules(self)
        self.module = load_library_sync_module('full_sync', {
            'xbmcgui': {},
            'resources.lib.library_sync.get_metadata': {
                'GetMetadataThread': object},
            'resources.lib.library_sync.fill_metadata_queue': {
                'FillMetadataQueue': object},
            'resources.lib.library_sync.process_metadata': {
                'ProcessMetadataThread': object},
            'resources.lib.library_sync.common': {
                'LibrarySyncMixin': fakes.LibrarySyncMixin,
                'PLAYLIST_SYNC_ENABLED': False},
            'resources.lib.library_sync.sections': {
                'Section': fakes.empty_section},
            'resources.lib.utils': {'log_time': lambda func: func},
            'resources.lib.timing': {},
            'resources.lib.backgroundthread': {
                'KillableThread': fakes.KillableThread},
            'resources.lib.variables': {},
            'resources.lib.app': {'SYNC': SimpleNamespace()},
            'resources.lib.plex_functions': {},
            'resources.lib.itemtypes': {},
            'resources.lib.path_ops': {},
        })
        self.module.PLAYSTATE_BATCH_SIZE = 2
        self.sync = self.module.FullSync.__new__(self.module.FullSync)
        fakes.KillableThread.__init__(self.sync)
        self.sync.current_time = 0
        self.sync.successful = True
        self.sync.update_progressbar = lambda *args: None
        self.section = fakes.FakeSection()
        self.section.number_of_items = 5

    def test_playstate_sync_releases_db_locks_between_batches(self):
        self.section.iterator = videos('abcde')
        self.sync.playstate_per_section(self.section)

        self.assertEqual(self.section.events, [
            'enter', 'a', 'b', 'exit',
            'enter', 'c', 'd', 'exit',
            'enter', 'e', 'exit',
        ])
        self.assertEqual(self.section.count, 5)
        self.assertEqual(len(self.sync.sleeps), 2)
        self.assertTrue(self.sync.successful)

    def test_failed_playstate_download_releases_db_locks(self):
        self.section.iterator = failing_videos()
        self.sync.playstate_per_section(self.section)

        self.assertEqual(self.section.events, [
            'enter', 'a', 'b', 'exit',
            'enter', 'exit',
        ])
        self.assertFalse(self.section.locked)
        self.assertFalse(self.sync.successful)


if __name__ == '__main__':
    unittest.main()
