import queue
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from . import fakes, isolate_modules, load_library_sync_module


class FakeProcessingQueue(object):
    """
    Delivers items in arrival groups. get() on an empty buffer 'blocks' until
    the next group (= PMS metadata download) arrives and records whether the
    DB locks were held meanwhile.
    """
    def __init__(self, section, arrivals, fast=False):
        # fast: every download arrives within get()'s timeout
        self.fast = fast
        self.section = section
        self.arrivals = [list(group) for group in arrivals]
        self.buffer = []
        self.blocked_while_locked = []
        self.timeouts_while_locked = []

    def empty(self):
        return not self.buffer

    def get(self, timeout=None):
        if not self.buffer:
            if timeout is not None and not self.fast:
                # The next download did not arrive within the timeout
                self.timeouts_while_locked.append(self.section.locked)
                raise queue.Empty
            self.blocked_while_locked.append(self.section.locked)
            self.buffer = self.arrivals.pop(0)
        return self.buffer.pop(0)

    def task_done(self):
        pass


def item(section, title):
    return {'section': section,
            'xml': [ET.Element('Video', title=title)],
            'children': None}


class ProcessMetadataLockTests(unittest.TestCase):
    def setUp(self):
        isolate_modules(self)

        class FakePlexDB(object):
            def __enter__(self):
                return SimpleNamespace(
                    update_section_last_sync=lambda *args: None)

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        self.module = load_library_sync_module('process_metadata', {
            'resources.lib.library_sync.common': {
                'LibrarySyncMixin': fakes.LibrarySyncMixin},
            'resources.lib.library_sync.sections': {
                'Section': fakes.empty_section},
            'resources.lib.plex_db': {'PlexDB': FakePlexDB},
            'resources.lib.backgroundthread': {
                'KillableThread': fakes.KillableThread},
            'resources.lib.app': {'SYNC': SimpleNamespace()},
        })
        self.section = fakes.FakeSection()

    def run_thread(self, arrivals, fast=False):
        processing_queue = FakeProcessingQueue(self.section, arrivals, fast)
        thread = self.module.ProcessMetadataThread(
            0, processing_queue, lambda *args: None)
        thread._run()
        return thread, processing_queue

    def test_db_locks_are_released_while_waiting_for_metadata(self):
        section = self.section
        _, processing_queue = self.run_thread([
            [item(section, 'a'), item(section, 'b')],
            [item(section, 'c')],
            [None],
        ])

        self.assertEqual(section.events, [
            'enter', 'a', 'b', 'exit',
            'enter', 'c', 'exit',
        ])
        self.assertEqual(processing_queue.blocked_while_locked,
                         [False, False, False])

    def test_db_locks_are_released_after_every_full_batch(self):
        self.module.COMMIT_TO_DB_EVERY_X_ITEMS = 2
        section = self.section
        thread, processing_queue = self.run_thread([
            [item(section, title) for title in 'abcde'] + [None],
        ])

        self.assertEqual(section.events, [
            'enter', 'a', 'b', 'exit',
            'enter', 'c', 'd', 'exit',
            'enter', 'e', 'exit',
        ])
        self.assertEqual(section.count, 5)
        # Yield to DB lock waiters after each full batch
        self.assertEqual(len(thread.sleeps), 2)
        self.assertEqual(processing_queue.blocked_while_locked, [False])


    def test_db_locks_are_kept_while_metadata_arrives_promptly(self):
        # Committing after every item when downloads are the bottleneck
        # would make an initial sync crawl on slow storage
        section = self.section
        _, processing_queue = self.run_thread([
            [item(section, 'a')],
            [item(section, 'b')],
            [item(section, 'c')],
            [None],
        ], fast=True)

        self.assertEqual(section.events, ['enter', 'a', 'b', 'c', 'exit'])
        self.assertEqual(processing_queue.timeouts_while_locked, [])

    def test_processed_items_are_reported_as_changes(self):
        section = self.section
        thread, _ = self.run_thread([[item(section, 'a'), None]])

        self.assertTrue(thread.changed)

    def test_nothing_processed_is_not_a_change(self):
        thread, _ = self.run_thread([[None]])

        self.assertFalse(thread.changed)
        self.assertTrue(thread.successful)


if __name__ == '__main__':
    unittest.main()
