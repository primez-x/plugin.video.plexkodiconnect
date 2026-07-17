#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger
import xbmc

from .. import utils, app, variables as v

LOG = getLogger('PLEX.sync')

PLAYLIST_SYNC_ENABLED = (v.DEVICE != 'Microsoft UWP' and
                         utils.settings('enablePlaylistSync') == 'true')


class LibrarySyncMixin(object):
    def suspend(self, block=False, timeout=None):
        """
        Let's NOT suspend sync threads but immediately terminate them
        """
        self.cancel()

    def wait_while_suspended(self):
        """
        Return immediately
        """
        return self.should_cancel()

    def run(self):
        app.APP.register_thread(self)
        LOG.debug('##===--- Starting %s ---===##', self.__class__.__name__)
        try:
            self._run()
        except Exception as err:
            LOG.error('Exception encountered: %s', err)
            utils.ERROR(notify=True)
        finally:
            app.APP.deregister_thread(self)
            LOG.debug('##===--- %s Stopped ---===##', self.__class__.__name__)


def update_kodi_library(video=True, music=True, changed_items=None):
    """
    Updates the Kodi library and thus refreshes the Kodi views and widgets.
    If changed_items is provided (list of (plex_id, plex_type) tuples),
    also signals skin-declared side-panel containers to reload when the
    changed item is relevant to the visible content.
    """
    if video:
        if not xbmc.getCondVisibility('Window.IsMedia'):
            xbmc.executebuiltin('UpdateLibrary(video)')
        else:
            # Prevent cursor from moving - refresh later
            xbmc.executebuiltin('Container.Refresh')
            app.APP.update_widgets = True
            # Signal skin-declared side-panel containers (e.g. Arctic Fuse
            # Combined view episode panels) to reload when a relevant episode
            # was synced, so stale cached listitem paths don't cause playback
            # errors. Skins opt in by declaring container IDs via:
            #   SetProperty(PKC.SidePanelContainers,530|531|...,10000)
            if changed_items:
                _refresh_sidepanel_containers(changed_items)
    if music:
        xbmc.executebuiltin('UpdateLibrary(music)')


def _refresh_sidepanel_containers(changed_items):
    """
    Bump cache-bust tokens for skin side-panel containers that are showing
    a TV show affected by the synced episodes.

    Contract (skin opt-in, no hard dependency):
      1. Skin declares its side-panel container IDs in
         Window(10000).Property(PKC.SidePanelContainers) as a pipe-separated
         list, e.g. "530|531|532", on window load.
      2. Skin appends a per-container sync token to each side-panel's
         content URL: $INFO[Window(10000).Property(PKC.SyncToken.<id>),&pkc=,]

    When PKC bumps the token for a container, the content URL changes, and
    Kodi's dynamic content container reloads with fresh listitems from the
    DB — discarding stale cached CVideoInfoTag objects (e.g. old file
    paths after a file replacement).
    """
    containers_prop = xbmc.getInfoLabel(
        'Window(10000).Property(PKC.SidePanelContainers)')
    if not containers_prop:
        return

    from .. import timing
    from ..plex_db import PlexDB

    for cid_str in containers_prop.split('|'):
        cid_str = cid_str.strip()
        if not cid_str:
            continue
        try:
            cid = int(cid_str)
        except ValueError:
            continue

        folder_path = xbmc.getInfoLabel('Container(%d).FolderPath' % cid)
        if not folder_path or 'videodb://tvshows/' not in folder_path:
            continue

        if not _is_sidepanel_relevant(folder_path, changed_items, PlexDB):
            continue

        token = str(timing.unix_timestamp())
        xbmc.executebuiltin(
            'SetProperty(PKC.SyncToken.%d,%s,10000)' % (cid, token))
        LOG.info('Signaled side-panel container %d reload: %s',
                 cid, folder_path)


def _is_sidepanel_relevant(folder_path, changed_items, PlexDB):
    """
    Check if any changed episode belongs to the TV show shown in the
    side-panel identified by folder_path. Uses the episode's
    grandparent_id (Kodi tvshow id) and compares against the show id
    parsed from the videodb:// URL.
    """
    import re
    match = re.search(r'/titles/(\d+)/', folder_path)
    if not match:
        return True  # Can't parse show ID; refresh to be safe

    path_show_id = int(match.group(1))

    for plex_id, plex_type in changed_items:
        if plex_type != v.PLEX_TYPE_EPISODE:
            continue
        with PlexDB(lock=False) as plexdb:
            episode = plexdb.episode(plex_id)
        if episode and episode.get('grandparent_id') == path_show_id:
            return True

    return False


def tag_last(iterable):
    """
    Given some iterable, returns (last, item), where last is only True if you
    are on the final iteration.
    """
    iterator = iter(iterable)
    gotone = False
    try:
        lookback = next(iterator)
        gotone = True
        while True:
            cur = next(iterator)
            yield False, lookback
            lookback = cur
    except StopIteration:
        if gotone:
            yield True, lookback
        raise StopIteration()
