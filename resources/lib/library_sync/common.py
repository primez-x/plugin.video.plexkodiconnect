#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger
import xbmc

from .. import utils, app, variables as v, gui_refresh

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
            app.APP.update_widgets = True
            app.APP.refresh_container = True
            refresh_container_if_pending()
            # Signal skin-declared side-panel containers (e.g. Arctic Fuse
            # Combined view episode panels) to reload when a relevant episode
            # was synced, so stale cached listitem paths don't cause playback
            # errors. Skins opt in by declaring container IDs via:
            #   SetProperty(PKC.SidePanelContainers,530|531|...,10000)
            if changed_items:
                gui_refresh.refresh_sidepanel_containers(changed_items)
    if music:
        xbmc.executebuiltin('UpdateLibrary(music)')


def refresh_container_if_pending():
    """
    Runs a Container.Refresh requested by update_kodi_library() once the
    container finished loading: refreshing a container that is still loading
    can crash Kodi. Called again from the service loop until done
    """
    if (not app.APP.refresh_container or
            xbmc.getCondVisibility('Container.IsUpdating')):
        return
    app.APP.refresh_container = False
    if xbmc.getCondVisibility('Window.IsMedia'):
        xbmc.executebuiltin('Container.Refresh')


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
