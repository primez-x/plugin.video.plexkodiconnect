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
            'resources.lib.playstate': {},
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



class FakeSyncSection(object):
    """A library section as threaded_get_generators() sees it"""
    def __init__(self, section_type):
        self.section_type = section_type
        self.sync_to_kodi = True
        self.section_id = 1
        self.name = 'Movies'
        self.last_sync = 1000
        self.count = 0
        self.number_of_items = 0
        self.iterator = None
        self.plex_type = None


class FakeIterator(object):
    def __init__(self, xmls):
        self.xmls = list(xmls)
        self.total = len(self.xmls)

    def __iter__(self):
        return iter(self.xmls)


class FullSyncLibraryRefreshTests(unittest.TestCase):
    """
    Runs FullSync._run() with all PMS, thread and DB access faked and counts
    how often Kodi is asked to reload its library views and widgets
    """
    def setUp(self):
        isolate_modules(self)
        self.refreshes = []
        self.events = []
        # What the fake PMS / Kodi DB report for this test
        self.items_processed = False
        self.process_successful = True
        self.playstate_xmls = []
        self.playstate_changed = False
        self.unsynced_playstate_items = False
        self.playstate_error = None
        self.stale_plex_ids = {}
        self.removed = []
        self.cancel_after_processing = False
        self.cancelled = False
        test = self

        class ProcessMetadataThread(object):
            def __init__(self, *args):
                self.successful = test.process_successful
                self.changed = test.items_processed

            def start(self):
                pass

            def join(self):
                test.cancelled = test.cancel_after_processing

        class Worker(object):
            def __init__(self, *args):
                pass

            def start(self):
                pass

            def join(self):
                pass

        class FunctionAsTask(object):
            def __init__(self, function, callback, *args, **kwargs):
                self.run = lambda: function(*args, **kwargs)

            def start(self):
                # Fill the section queue right away instead of in a thread
                self.run()

        class PlaystateContext(object):
            def __init__(self, current_time):
                self.userdata_changed = False
                self.plexdb = SimpleNamespace(
                    update_last_sync=lambda *args: None)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def update_userdata(self, xml, plex_type):
                if test.playstate_error:
                    raise test.playstate_error
                if test.unsynced_playstate_items:
                    return False
                if test.playstate_changed:
                    self.userdata_changed = True
                return True

            def add_update(self, xml, **kwargs):
                pass

        def get_sync_section(section, plex_type):
            section.plex_type = plex_type
            section.context = PlaystateContext
            return section

        def get_section_iterator(section_id, plex_type, updated_at,
                                 last_viewed_at):
            # New and changed items are reported via ProcessMetadataThread
            if last_viewed_at is None and updated_at is not None:
                return FakeIterator([])
            return FakeIterator(test.playstate_xmls)

        def deletion_context(plex_type):
            class Context(object):
                def __init__(self, current_time):
                    self.plexdb = SimpleNamespace(
                        plex_id_by_last_sync=lambda *args: list(
                            test.stale_plex_ids.pop(plex_type, [])))

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_value, traceback):
                    return False

                def remove(self, plex_id, plex_type):
                    test.removed.append((plex_id, plex_type))
            return Context

        self.app = SimpleNamespace(
            SYNC=SimpleNamespace(enable_music=False,
                                 sections=[FakeSyncSection('movie'),
                                           FakeSyncSection('show')]),
            APP=SimpleNamespace(is_playing_video=False))
        self.module = load_library_sync_module('full_sync', {
            'xbmcgui': {},
            'resources.lib.library_sync.get_metadata': {
                'GetMetadataThread': Worker},
            'resources.lib.library_sync.fill_metadata_queue': {
                'FillMetadataQueue': Worker},
            'resources.lib.library_sync.process_metadata': {
                'ProcessMetadataThread': ProcessMetadataThread},
            'resources.lib.library_sync.common': {
                'LibrarySyncMixin': fakes.LibrarySyncMixin,
                'PLAYLIST_SYNC_ENABLED': False,
                'update_kodi_library': (
                    lambda **kwargs: test.refreshes.append(kwargs))},
            'resources.lib.library_sync.sections': {
                'Section': fakes.empty_section,
                'sync_from_pms': lambda parent_self, **kwargs: True,
                'get_sync_section': get_sync_section},
            'resources.lib.utils': {
                'log_time': lambda func: func,
                'settings': lambda key: '1' if key == 'syncThreadNumber'
                else 'false',
                'ERROR': lambda **kwargs: None,
                'dialog': lambda *args, **kwargs: None,
                'lang': lambda string_id: ''},
            'resources.lib.timing': {'plex_now': lambda: 2000},
            'resources.lib.backgroundthread': {
                'KillableThread': fakes.KillableThread,
                'FunctionAsTask': FunctionAsTask,
                'ProcessingQueue': lambda maxsize: None},
            'resources.lib.variables': {
                'PLEX_TYPE_MOVIE': 'movie',
                'PLEX_TYPE_SHOW': 'show',
                'PLEX_TYPE_SEASON': 'season',
                'PLEX_TYPE_EPISODE': 'episode',
                'PLEX_TYPE_ARTIST': 'artist',
                'PLEX_TYPE_ALBUM': 'album',
                'PLEX_TYPE_SONG': 'song',
                'DB_PLEX_PATH': 'plex.db',
                'DB_PLEX_COPY_PATH': 'plex-copy.db'},
            'resources.lib.app': {'SYNC': self.app.SYNC,
                                  'APP': self.app.APP},
            'resources.lib.plex_functions': {
                'get_section_iterator': get_section_iterator},
            'resources.lib.itemtypes': {
                name: deletion_context(plex_type) for name, plex_type in (
                    ('Movie', 'movie'), ('Show', 'show'),
                    ('Season', 'season'), ('Episode', 'episode'),
                    ('Artist', 'artist'), ('Album', 'album'),
                    ('Song', 'song'))},
            'resources.lib.path_ops': {'copyfile': lambda *args: None},
            'resources.lib.playstate': {
                'prefetch_all_shared': lambda: test.events.append('prefetch')},
        })
        self.results = []
        self.sync = self.module.FullSync(repair=False,
                                         callback=self.results.append,
                                         show_dialog=False)
        self.sync.should_cancel = lambda: test.cancelled

    def run_sync(self):
        self.sync._run()
        return self.refreshes

    def test_shared_episode_playstates_are_fetched_before_each_playstate_pass(self):
        # Before the DB locks are taken: before adding items, before the
        # playstate pass for recently watched items and before the pass for
        # every item
        self.run_sync()
        self.assertEqual(self.events, ['prefetch'] * 3)

    def test_sync_without_changes_does_not_reload_the_library(self):
        # Every item's playstate is checked, none differs from Kodi's
        self.playstate_xmls = list(videos('abc'))

        self.assertEqual(self.run_sync(), [])
        self.assertEqual(self.results, [True])

    def test_changed_item_reloads_the_library_once_at_the_end(self):
        self.items_processed = True
        self.playstate_xmls = list(videos('abc'))

        self.assertEqual(self.run_sync(), [{'video': True, 'music': False}])
        self.assertEqual(self.results, [True])

    def test_deleted_item_alone_reloads_the_library(self):
        self.stale_plex_ids = {'episode': [7]}

        self.assertEqual(self.run_sync(), [{'video': True, 'music': False}])
        self.assertEqual(self.removed, [(7, 'episode')])

    def test_changed_playstate_alone_reloads_the_library(self):
        self.playstate_xmls = list(videos('abc'))
        self.playstate_changed = True

        self.assertEqual(self.run_sync(), [{'video': True, 'music': False}])

    def test_playstate_of_unsynced_item_reloads_the_library(self):
        self.playstate_xmls = list(videos('a'))
        self.unsynced_playstate_items = True

        self.assertEqual(self.run_sync(), [{'video': True, 'music': False}])

    def test_exception_mid_sync_reloads_the_library_once(self):
        self.playstate_xmls = list(videos('abc'))
        self.playstate_error = ValueError('Unexpected PMS answer')

        with self.assertRaises(ValueError):
            self.run_sync()
        self.assertEqual(self.refreshes, [{'video': True, 'music': False}])

    def test_failed_item_processing_reloads_the_library_once(self):
        self.process_successful = False

        self.assertEqual(self.run_sync(), [{'video': True, 'music': False}])
        self.assertEqual(self.results, [False])

    def test_sync_cancelled_mid_way_reloads_the_library_once(self):
        # Kodi might have been told to shut down while items were written
        self.cancel_after_processing = True

        self.assertEqual(self.run_sync(), [{'video': True, 'music': False}])

    def test_music_library_is_reloaded_only_with_music_sync(self):
        self.app.SYNC.enable_music = True
        self.items_processed = True

        self.assertEqual(self.run_sync(), [{'video': True, 'music': True}])


if __name__ == '__main__':
    unittest.main()
