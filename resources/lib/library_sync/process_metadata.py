# -*- coding: utf-8 -*-
from logging import getLogger
import queue

from . import common, sections
from ..plex_db import PlexDB
from .. import backgroundthread, app

LOG = getLogger('PLEX.sync.process_metadata')

COMMIT_TO_DB_EVERY_X_ITEMS = 500
# Seconds to pause after releasing the DB locks at a full batch so threads
# waiting for them (e.g. Kodi playstate callbacks) can get a turn
DB_LOCK_YIELD_SECONDS = 0.01
# Seconds to keep the DB locks while waiting for the next PMS metadata item.
# Releasing on every momentarily empty queue would commit after every single
# item whenever downloads are the bottleneck (e.g. an initial sync)
QUEUE_WAIT_WITH_DB_LOCKS_SECONDS = 1.0
# Returned by _get() when no item arrived within its timeout
_TIMED_OUT = object()


class ProcessMetadataThread(common.LibrarySyncMixin,
                            backgroundthread.KillableThread):
    """
    Invoke once in order to process the received PMS metadata xmls
    """
    def __init__(self, current_time, processing_queue, update_progressbar):
        self.current_time = current_time
        self.processing_queue = processing_queue
        self.update_progressbar = update_progressbar
        self.last_section = sections.Section()
        self.successful = True
        # True once an item was added to or updated in the Kodi DB
        self.changed = False
        super(ProcessMetadataThread, self).__init__()

    def start_section(self, section):
        if section != self.last_section:
            if self.last_section:
                self.finish_last_section()
            LOG.debug('Start or continue processing section %s', section)
            self.last_section = section
            # Warn the user for this new section if we cannot access a file
            app.SYNC.path_verified = False
        else:
            LOG.debug('Resume processing section %s', section)

    def finish_last_section(self):
        if (not self.should_cancel() and self.last_section and
                self.last_section.sync_successful):
            # Check for should_cancel() because we cannot be sure that we
            # processed every item of the section
            with PlexDB() as plexdb:
                # Set the new time mark for the next delta sync
                plexdb.update_section_last_sync(self.last_section.section_id,
                                                self.current_time)
            LOG.info('Finished processing section successfully: %s',
                     self.last_section)
        elif self.last_section and not self.last_section.sync_successful:
            LOG.warn('Sync not successful for section %s', self.last_section)
            self.successful = False

    def _get(self, timeout=None):
        item = {'xml': None}
        while item and item['xml'] is None:
            try:
                item = self.processing_queue.get(timeout=timeout)
            except queue.Empty:
                return _TIMED_OUT
            self.processing_queue.task_done()
        return item

    def _run(self):
        # There are 2 sentinels: None for aborting/ending this thread, the dict
        # {'section': section, 'xml': None} for skipped/invalid items
        item = self._get()
        if item:
            section = item['section']
            processed = 0
            self.start_section(section)
        while not self.should_cancel():
            if item is None:
                break
            elif item['section'] != section:
                # We received an entirely new section
                self.start_section(item['section'])
                section = item['section']
            batch_done = False
            # Entering the context acquires the Plex and Kodi DB locks,
            # leaving it commits and releases them. Never keep them for an
            # entire section: other writers (e.g. Kodi playstate callbacks)
            # would block for all of it
            with section.context(self.current_time) as context:
                while not self.should_cancel():
                    if item is None or item['section'] != section:
                        break
                    self.update_progressbar(section,
                                            item['xml'][0].get('title'),
                                            section.count)
                    # Set before writing: a failed write may have changed
                    # the Kodi DB partially
                    self.changed = True
                    context.add_update(item['xml'][0],
                                       section_name=section.name,
                                       section_id=section.section_id,
                                       children=item['children'])
                    processed += 1
                    section.count += 1
                    if processed >= COMMIT_TO_DB_EVERY_X_ITEMS:
                        batch_done = True
                        break
                    item = self._get(timeout=QUEUE_WAIT_WITH_DB_LOCKS_SECONDS)
                    if item is _TIMED_OUT:
                        # Don't wait for PMS metadata downloads while
                        # holding the DB locks
                        break
            if batch_done or item is _TIMED_OUT:
                if batch_done:
                    self.sleep(DB_LOCK_YIELD_SECONDS)
                processed = 0
                item = self._get()
        self.finish_last_section()
