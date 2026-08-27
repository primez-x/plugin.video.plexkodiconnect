#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PKC Kodi Monitoring implementation
"""
from logging import getLogger
from json import loads
import copy
from threading import Lock
from time import monotonic
import xbmc

from .plex_api import API
from .plex_db import PlexDB
from .kodi_db import KodiVideoDB
from . import kodi_db
from .downloadutils import DownloadUtils as DU
from . import utils, timing, plex_functions as PF
from . import json_rpc as js, playlist_func as PL
from . import backgroundthread, app, variables as v
from . import exceptions
from . import skip_plex_markers
from . import upnext
from . import gui_refresh

LOG = getLogger('PLEX.kodimonitor')

DISCOVER_SIGNAL_SENDER = 'plugin.video.plexkodiconnect.SIGNAL'
DISCOVER_REFRESH_MESSAGE = 'discover_cache_refresh'
DISCOVER_MAINTENANCE_MESSAGE = 'discover_cache_maintenance'

WAIT_BEFORE_INIT_STREAMS = 6
ADDITIONAL_WAIT_BEFORE_INIT_STREAMS = 10
UPNEXT_HANDOFF_TOKEN_SECONDS = 7200.0
UPNEXT_HANDOFF_START_SECONDS = 120.0
UPNEXT_HANDOFF_SUPPRESSION_SECONDS = 30.0
PLAYBACK_RESTORE_QUIET_SECONDS = 0.25
PLAYBACK_RESTORE_RETRY_SECONDS = 0.5
PLAYBACK_RESTORE_MAX_ATTEMPTS = 3
PLAYBACK_RESTORE_MAX_PASSES = 8
STRANDED_PLAYBACK_WINDOW_RECOVERY_DELAY = 1
STRANDED_PLAYBACK_WINDOW_IDS = {
    12005,  # Fullscreen video
    12901,  # Fullscreen OSD / videoosd
}
STRANDED_PLAYBACK_WINDOW_LABELS = {
    'Fullscreen OSD',
    'Fullscreen Video',
    'Full screen video',
}
STRANDED_PLAYBACK_WINDOW_RECOVERY_COMMANDS = (
    'Dialog.Close(busydialog, true)',
    'Dialog.Close(videoosd, true)',
    'Dialog.Close(fullscreeninfo, true)',
    'ActivateWindow(Home)',
)
_ACTIVE_UPNEXT_HANDOFF = None
_PENDING_PLAYBACK_RESTORES = {}
_PLAYBACK_RESTORE_LOCK = Lock()
_NEXT_PLAYBACK_RESTORE_REVISION = 0
_COMPLETED_UPNEXT_HANDOFFS = {}
_PMS_PLAYSTATE_LOCK = Lock()
_PMS_PLAYSTATE_REVISIONS = {}
_PMS_PLAYSTATE_ITEM_LOCKS = {}


class PMSPlaystateTask(backgroundthread.FunctionAsTask):
    """Send only the latest queued PMS playstate for one Plex item."""
    def __init__(self, plex_id, state, revision):
        self.plex_id = plex_id
        self.state = state
        self.revision = revision
        super(PMSPlaystateTask, self).__init__(
            PF.scrobble, None, plex_id, state)

    def run(self):
        key = str(self.plex_id)
        with _PMS_PLAYSTATE_LOCK:
            if _PMS_PLAYSTATE_REVISIONS.get(key) != self.revision:
                return
            item_lock = _PMS_PLAYSTATE_ITEM_LOCKS[key]
        with item_lock:
            with _PMS_PLAYSTATE_LOCK:
                if _PMS_PLAYSTATE_REVISIONS.get(key) != self.revision:
                    return
            PF.scrobble(self.plex_id, self.state)


def _queue_pms_playstate(plex_id, state):
    if not plex_id:
        return None
    key = str(plex_id)
    with _PMS_PLAYSTATE_LOCK:
        revision = _PMS_PLAYSTATE_REVISIONS.get(key, 0) + 1
        _PMS_PLAYSTATE_REVISIONS[key] = revision
        _PMS_PLAYSTATE_ITEM_LOCKS.setdefault(key, Lock())
    task = PMSPlaystateTask(plex_id, state, revision)
    backgroundthread.BGThreader.addTask(task)
    return task


def _activate_upnext_handoff(next_item, now=None):
    """Arm delayed-update protection for a proven Up Next invocation."""
    global _ACTIVE_UPNEXT_HANDOFF
    now = monotonic() if now is None else now
    active = _ACTIVE_UPNEXT_HANDOFF
    current = getattr(app.PLAYSTATE, 'item', None)
    if active and next_item and current:
        same_item = active.get('next_item') == (
            next_item.kodi_id, next_item.kodi_type) and \
            str(active.get('next_plex_id')) == str(next_item.plex_id) and \
            (current.kodi_id, current.kodi_type) == active['next_item'] and \
            str(getattr(current, 'plex_id', None)) == str(
                active.get('next_plex_id'))
        protection_active = active.get('expires_at', 0) >= now or \
            active.get('watched_queued', False)
    else:
        same_item = False
        protection_active = False
    if same_item and protection_active:
        LOG.debug('Keeping existing Up Next handoff for duplicate playback '
                  'of Kodi item %s/%s', next_item.kodi_type, next_item.kodi_id)
        return True
    expected = getattr(app.PLAYSTATE, 'expected_upnext_handoff', None)
    started = getattr(app.PLAYSTATE, 'started_upnext_handoff', None)
    if active:
        _cancel_playback_restore(
            active['previous_item'], _upnext_handoff_key(active))
    if expected:
        _cancel_playback_restore(
            (expected.get('previous_kodi_id'),
             expected.get('previous_kodi_type')),
            _upnext_handoff_key(expected))
    app.PLAYSTATE.expected_upnext_handoff = None
    app.PLAYSTATE.started_upnext_handoff = None
    # Every playback start invalidates protection from an older transition.
    _ACTIVE_UPNEXT_HANDOFF = None
    if not expected or not started or not next_item:
        return False
    created_at = expected.get('created_at')
    started_at = started.get('created_at')
    if created_at is None or not 0 <= now - created_at <= \
            UPNEXT_HANDOFF_TOKEN_SECONDS:
        return False
    if started_at is None or not 0 <= now - started_at <= \
            UPNEXT_HANDOFF_START_SECONDS:
        return False
    completion_seen = bool(expected.get('completion_seen'))
    if expected.get('token') != started.get('token'):
        return False
    if str(expected.get('next_plex_id')) != str(started.get('plex_id')) or \
            str(expected.get('next_plex_id')) != str(next_item.plex_id):
        return False
    if expected.get('previous_kodi_id') != \
            started.get('previous_kodi_id') or \
            expected.get('previous_kodi_type') != \
            started.get('previous_kodi_type') or \
            str(expected.get('previous_plex_id')) != str(
                started.get('previous_plex_id')) or \
            expected.get('previous_generation') != \
            started.get('previous_generation'):
        return False
    watched_queued = bool(expected.get('watched_queued'))
    queue_watched = completion_seen and not watched_queued
    _ACTIVE_UPNEXT_HANDOFF = {
        'token': expected['token'],
        'previous_item': (
            expected['previous_kodi_id'], expected['previous_kodi_type']),
        'previous_plex_id': expected['previous_plex_id'],
        'next_item': (next_item.kodi_id, next_item.kodi_type),
        'next_plex_id': next_item.plex_id,
        'previous_generation': expected['previous_generation'],
        'expires_at': now + UPNEXT_HANDOFF_SUPPRESSION_SECONDS,
        'completion_seen': completion_seen,
        'watched_queued': watched_queued or queue_watched,
        'reset_seen': bool(expected.get('reset_seen')),
        'local_watched_repaired': False,
    }
    if completion_seen:
        _remember_upnext_completion(_ACTIVE_UPNEXT_HANDOFF)
    if queue_watched:
        _queue_pms_playstate(expected['previous_plex_id'], 'watched')
    if _ACTIVE_UPNEXT_HANDOFF['reset_seen']:
        _force_kodi_watched(
            _ACTIVE_UPNEXT_HANDOFF,
            expected['previous_kodi_id'],
            expected['previous_kodi_type'],
            expected['token'])
    LOG.debug('Verified Up Next handoff for Kodi item %s/%s -> %s/%s',
              expected['previous_kodi_type'], expected['previous_kodi_id'],
              next_item.kodi_type, next_item.kodi_id)
    return True


def _is_upnext_handoff_update(kodi_id, kodi_type, now=None):
    now = monotonic() if now is None else now
    handoff = _ACTIVE_UPNEXT_HANDOFF
    if not handoff or handoff['previous_item'] != (kodi_id, kodi_type):
        return False
    if handoff['expires_at'] >= now:
        return True
    return bool(handoff.get('watched_queued'))


def _expected_upnext_handoff_for_update(kodi_id, kodi_type, now):
    expected = getattr(app.PLAYSTATE, 'expected_upnext_handoff', None)
    if not expected or expected.get('previous_kodi_id') != kodi_id or \
            expected.get('previous_kodi_type') != kodi_type:
        return None
    created_at = expected.get('created_at')
    if created_at is None or not 0 <= now - created_at <= \
            UPNEXT_HANDOFF_TOKEN_SECONDS:
        return None
    # Before the next Player.OnPlay, require the still-current outgoing item.
    # Once playback_starter has recorded a start, its identity proof survives
    # the resolver replacing PLAYSTATE.item.
    started = getattr(app.PLAYSTATE, 'started_upnext_handoff', None)
    if started is None:
        outgoing = getattr(app.PLAYSTATE, 'item', None)
        if not outgoing or (outgoing.kodi_id, outgoing.kodi_type) != \
                (expected.get('previous_kodi_id'),
                 expected.get('previous_kodi_type')) or \
                str(getattr(outgoing, 'plex_id', None)) != str(
                    expected.get('previous_plex_id')) or \
                getattr(outgoing, 'pkc_playback_generation', None) != \
                expected.get('previous_generation') or \
                getattr(app.PLAYSTATE, 'playback_generation', None) != \
                expected.get('previous_generation'):
            return None
        return expected
    started_at = started.get('created_at')
    if started_at is None or not 0 <= now - started_at <= \
            UPNEXT_HANDOFF_START_SECONDS:
        return None
    if expected.get('token') != started.get('token') or \
            str(expected.get('next_plex_id')) != str(
                started.get('plex_id')):
        return None
    if expected.get('previous_kodi_id') != \
            started.get('previous_kodi_id') or \
            expected.get('previous_kodi_type') != \
            started.get('previous_kodi_type') or \
            str(expected.get('previous_plex_id')) != str(
                started.get('previous_plex_id')) or \
            expected.get('previous_generation') != \
            started.get('previous_generation'):
        return None
    return expected


def _upnext_handoff_for_update(kodi_id, kodi_type, now=None):
    now = monotonic() if now is None else now
    if _is_upnext_handoff_update(kodi_id, kodi_type, now):
        return _ACTIVE_UPNEXT_HANDOFF
    expected = _expected_upnext_handoff_for_update(kodi_id, kodi_type, now)
    if expected is not None:
        return expected
    return _completed_upnext_handoff_for_update(kodi_id, kodi_type)


def _upnext_handoff_key(handoff):
    return handoff.get('token') or (
        'active' if 'next_item' in handoff else 'expected',
        handoff.get('previous_item') or (
            handoff.get('previous_kodi_id'), handoff.get('previous_kodi_type')),
        handoff.get('previous_generation'))


def _upnext_previous_item(handoff):
    return handoff.get('previous_item') or (
        handoff.get('previous_kodi_id'), handoff.get('previous_kodi_type'))


def _remember_upnext_completion(handoff):
    previous_item = _upnext_previous_item(handoff)
    if previous_item[0] is not None and previous_item[1] is not None:
        _COMPLETED_UPNEXT_HANDOFFS[previous_item] = copy.deepcopy(handoff)


def _completed_upnext_handoff_for_update(kodi_id, kodi_type):
    return _COMPLETED_UPNEXT_HANDOFFS.get((kodi_id, kodi_type))


def _upnext_watched_reported(handoff):
    return bool(handoff.get('completion_seen') or
                handoff.get('watched_queued'))


def _claim_upnext_watched(handoff, kodi_id, kodi_type, now=None):
    """Claim or latch the single watched report for an Up Next handoff."""
    now = monotonic() if now is None else now
    with _PLAYBACK_RESTORE_LOCK:
        handoff_key = _upnext_handoff_key(handoff)
        pending = _PENDING_PLAYBACK_RESTORES.get((kodi_id, kodi_type))
        if pending is not None and pending.get('handoff_key') == handoff_key:
            del _PENDING_PLAYBACK_RESTORES[(kodi_id, kodi_type)]
        if handoff is not _ACTIVE_UPNEXT_HANDOFF:
            if handoff.get('completion_seen'):
                return False
            handoff['completion_seen'] = True
            handoff['watched_queued'] = True
            _remember_upnext_completion(handoff)
            return True
        if handoff.get('watched_queued'):
            return False
        handoff['completion_seen'] = True
        handoff['watched_queued'] = True
        _remember_upnext_completion(handoff)
        return True


def _cancel_playback_restore(item, handoff_key=None):
    with _PLAYBACK_RESTORE_LOCK:
        state = _PENDING_PLAYBACK_RESTORES.get(item)
        if state is None or (handoff_key is not None and
                             state.get('handoff_key') != handoff_key):
            return None
        return _PENDING_PLAYBACK_RESTORES.pop(item)


def _request_playback_restore(kodi_id, kodi_type, handoff_key=None,
                              handoff=None):
    global _NEXT_PLAYBACK_RESTORE_REVISION
    item = (kodi_id, kodi_type)
    with _PLAYBACK_RESTORE_LOCK:
        if handoff is not None and _upnext_watched_reported(handoff):
            return False
        state = _PENDING_PLAYBACK_RESTORES.get(item)
        if state is not None and state.get('handoff_key') == handoff_key:
            _NEXT_PLAYBACK_RESTORE_REVISION += 1
            state['revision'] = _NEXT_PLAYBACK_RESTORE_REVISION
            return False
        _NEXT_PLAYBACK_RESTORE_REVISION += 1
        _PENDING_PLAYBACK_RESTORES[item] = {
            'revision': _NEXT_PLAYBACK_RESTORE_REVISION,
            'handoff_key': handoff_key,
        }
        return True


def _playback_restore_revision(item, handoff_key=None):
    with _PLAYBACK_RESTORE_LOCK:
        state = _PENDING_PLAYBACK_RESTORES.get(item)
        if state is None or state.get('handoff_key') != handoff_key:
            return None
        return state['revision']


def _finish_playback_restore(item, revision, handoff_key=None):
    with _PLAYBACK_RESTORE_LOCK:
        state = _PENDING_PLAYBACK_RESTORES.get(item)
        if state is None or state.get('handoff_key') != handoff_key or \
                state['revision'] != revision:
            return False
        del _PENDING_PLAYBACK_RESTORES[item]
        return True


def _release_playback_restore(item, handoff_key=None):
    with _PLAYBACK_RESTORE_LOCK:
        state = _PENDING_PLAYBACK_RESTORES.get(item)
        if state is None or state.get('handoff_key') != handoff_key:
            return None
        return _PENDING_PLAYBACK_RESTORES.pop(item)


def _requeue_playback_restore_if_changed(
        item, handoff_key, last_attempted_revision):
    """Keep a changed restore generation live without a cancellation gap."""
    with _PLAYBACK_RESTORE_LOCK:
        state = _PENDING_PLAYBACK_RESTORES.get(item)
        if state is None or state.get('handoff_key') != handoff_key:
            return False
        if state['revision'] == last_attempted_revision:
            del _PENDING_PLAYBACK_RESTORES[item]
            return False
        return True


def _write_kodi_watched(db_item):
    lastplayed = timing.kodi_now()
    with kodi_db.KodiVideoDB() as kodidb:
        kodidb.set_watched(db_item['kodi_fileid'], lastplayed)
        if db_item.get('kodi_fileid_2'):
            kodidb.set_watched(db_item['kodi_fileid_2'], lastplayed)


def _write_kodi_unwatched(db_item):
    with kodi_db.KodiVideoDB() as kodidb:
        kodidb.set_resume(db_item['kodi_fileid'], 0.0, 0.0, 0, None)
        if db_item.get('kodi_fileid_2'):
            kodidb.set_resume(db_item['kodi_fileid_2'], 0.0, 0.0, 0, None)


def _refresh_kodi_playstate(db_item):
    xbmc.executebuiltin('Container.Refresh')
    if db_item['plex_type'] == v.PLEX_TYPE_EPISODE:
        gui_refresh.refresh_sidepanel_containers((
            (db_item['plex_id'], db_item['plex_type']),
        ))


def _repair_kodi_watched(db_item):
    with _PLAYBACK_RESTORE_LOCK:
        _write_kodi_watched(db_item)
    _refresh_kodi_playstate(db_item)


def _force_kodi_unwatched(kodi_id, kodi_type):
    """Reassert an explicit unwatch after canceling a stale restore."""
    with PlexDB(lock=False) as plexdb:
        db_item = plexdb.item_by_kodi_id(kodi_id, kodi_type)
    if not db_item:
        LOG.error('Could not repair unwatched state for Kodi item %s/%s: '
                  'Plex mapping not found', kodi_type, kodi_id)
        return False
    try:
        with _PLAYBACK_RESTORE_LOCK:
            _write_kodi_unwatched(db_item)
    except Exception:
        LOG.exception('Could not repair unwatched state for Kodi item %s/%s',
                      kodi_type, kodi_id)
        return False
    _refresh_kodi_playstate(db_item)
    LOG.info('Repaired Kodi unwatched state after explicit update for Plex '
             'item %s', db_item['plex_id'])
    return True


def _force_kodi_watched(handoff, kodi_id, kodi_type, handoff_key):
    """Repair Kodi after a stale reset without rereading PMS playstate."""
    with PlexDB(lock=False) as plexdb:
        db_item = plexdb.item_by_kodi_id(kodi_id, kodi_type)
    if not db_item:
        LOG.error('Could not repair watched state for Kodi item %s/%s: '
                  'Plex mapping not found', kodi_type, kodi_id)
        return False
    try:
        with _PLAYBACK_RESTORE_LOCK:
            expected = getattr(
                app.PLAYSTATE, 'expected_upnext_handoff', None)
            active = handoff is _ACTIVE_UPNEXT_HANDOFF
            expected_only = handoff is expected
            if active:
                valid_handoff = _is_upnext_handoff_update(
                    kodi_id, kodi_type)
            elif expected_only:
                valid_handoff = _expected_upnext_handoff_for_update(
                    kodi_id, kodi_type, monotonic()) is handoff
            elif _completed_upnext_handoff_for_update(
                    kodi_id, kodi_type) is handoff:
                valid_handoff = True
            else:
                valid_handoff = False
            if not valid_handoff or \
                    _upnext_handoff_key(handoff) != handoff_key or \
                    str(db_item.get('plex_id')) != str(
                        handoff.get('previous_plex_id')) or \
                    not handoff.get('watched_queued'):
                return False
            _write_kodi_watched(db_item)
            handoff['reset_seen'] = False
    except Exception:
        LOG.exception('Could not repair watched state for Kodi item %s/%s',
                      kodi_type, kodi_id)
        return False
    _refresh_kodi_playstate(db_item)
    LOG.info('Repaired Kodi watched state after completed Up Next handoff '
             'for Plex item %s', db_item['plex_id'])
    return True


class KodiMonitor(xbmc.Monitor):
    """
    PKC implementation of the Kodi Monitor class. Invoke only once.
    """

    def __init__(self):
        self._already_slept = False
        xbmc.Monitor.__init__(self)
        for playerid in app.PLAYSTATE.player_states:
            app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
        LOG.info("Kodi monitor started.")

    def onScanStarted(self, library):
        """
        Will be called when Kodi starts scanning the library
        """
        LOG.debug("Kodi library scan %s running.", library)

    def onScanFinished(self, library):
        """
        Will be called when Kodi finished scanning the library
        """
        LOG.debug("Kodi library scan %s finished.", library)

    def onSettingsChanged(self):
        """
        Monitor the PKC settings for changes made by the user
        """
        LOG.debug('PKC settings change detected')
        skip_plex_markers.reset_runtime()

    def onNotification(self, sender, method, data):
        """
        Called when a bunch of different stuff happens on the Kodi side
        """
        if data:
            data = loads(data)
            LOG.debug("Method: %s Data: %s", method, data)

        if method == "Player.OnPlay":
            with app.APP.lock_playqueues:
                self.PlayBackStart(data)
        elif method == 'Player.OnAVChange':
            with app.APP.lock_playqueues:
                self._on_av_change(data)
        elif method == "Player.OnStop":
            with app.APP.lock_playqueues:
                _playback_cleanup(ended=data.get('end'))
        elif method in ('Player.OnSeek', 'Player.OnPause', 'Player.OnResume'):
            # A seek or playback-state transition invalidates the next marker
            # deadline. The next service tick must re-read player time once,
            # then can return to deadline-driven scheduling.
            skip_plex_markers.reset_runtime()
        elif sender == DISCOVER_SIGNAL_SENDER and method == DISCOVER_REFRESH_MESSAGE:
            event = getattr(app.APP, 'discover_refresh_event', None)
            if event is not None:
                event.set()
        elif sender == DISCOVER_SIGNAL_SENDER and method == DISCOVER_MAINTENANCE_MESSAGE:
            event = getattr(app.APP, 'discover_maintenance_event', None)
            if event is not None:
                event.set()
        elif method == 'Playlist.OnAdd':
            if 'item' in data and data['item'].get('type') == v.KODI_TYPE_SHOW:
                # Hitting the "browse" button on tv show info dialog
                # Hence show the tv show directly
                xbmc.executebuiltin("Dialog.Close(all, true)")
                js.activate_window('videos',
                                   'videodb://tvshows/titles/%s/' % data['item']['id'])
            with app.APP.lock_playqueues:
                self._playlist_onadd(data)
        elif method == 'Playlist.OnRemove':
            self._playlist_onremove(data)
        elif method == 'Playlist.OnClear':
            with app.APP.lock_playqueues:
                self._playlist_onclear(data)
        elif method == "VideoLibrary.OnUpdate":
            with app.APP.lock_playqueues:
                _videolibrary_onupdate(data)
        elif method == "VideoLibrary.OnRemove":
            pass
        elif method == "System.OnSleep":
            # Connection is going to sleep
            LOG.info("Marking the server as offline. SystemOnSleep activated.")
        elif method == "System.OnWake":
            # Allow network to wake up
            self.waitForAbort(10)
            app.CONN.online = False
        elif method == "GUI.OnScreensaverDeactivated":
            if utils.settings('dbSyncScreensaver') == "true":
                self.waitForAbort(5)
                app.SYNC.run_lib_scan = 'full'
        elif method == "System.OnQuit":
            LOG.info('Kodi OnQuit detected - shutting down')
            app.APP.stop_pkc = True

    def _playlist_onadd(self, data):
        """
        Called if an item is added to a Kodi playlist. Example data dict:
        {
            u'item': {
                u'type': u'movie',
                u'id': 2},
            u'playlistid': 1,
            u'position': 0
        }
        Will NOT be called if playback initiated by Kodi widgets
        """
        pass

    def _playlist_onremove(self, data):
        """
        Called if an item is removed from a Kodi playlist. Example data dict:
        {
            u'playlistid': 1,
            u'position': 0
        }
        """
        pass

    @staticmethod
    def _playlist_onclear(data):
        """
        Called if a Kodi playlist is cleared. Example data dict:
        {
            u'playlistid': 1,
        }
        """
        playqueue = app.PLAYQUEUES[data['playlistid']]
        if not playqueue.is_pkc_clear():
            playqueue.pkc_edit = True
            playqueue.clear(kodi=False)
        else:
            LOG.debug('Detected PKC clear - ignoring')

    @staticmethod
    def _get_ids(kodi_id, kodi_type, path):
        """
        Returns the tuple (plex_id, plex_type) or (None, None)
        """
        # No Kodi id returned by Kodi, even if there is one. Ex: Widgets
        plex_id = None
        plex_type = None
        # If using direct paths and starting playback from a widget
        if not kodi_id and kodi_type and path:
            kodi_id, _ = kodi_db.kodiid_from_filename(path, kodi_type)
        if kodi_id:
            with PlexDB(lock=False) as plexdb:
                db_item = plexdb.item_by_kodi_id(kodi_id, kodi_type)
            if db_item:
                plex_id = db_item['plex_id']
                plex_type = db_item['plex_type']
        return plex_id, plex_type

    @staticmethod
    def _add_remaining_items_to_playlist(playqueue):
        """
        Adds all but the very first item of the Kodi playlist to the Plex
        playqueue
        """
        items = js.playlist_get_items(playqueue.playlistid)
        if not items:
            LOG.error('Could not retrieve Kodi playlist items')
            return
        # Remove first item
        items.pop(0)
        try:
            for i, item in enumerate(items):
                PL.add_item_to_plex_playqueue(playqueue, i + 1, kodi_item=item)
        except exceptions.PlaylistError:
            LOG.info('Could not build Plex playlist for: %s', items)

    def _json_item(self, playerid):
        """
        Uses JSON RPC to get the playing item's info and returns the tuple
            kodi_id, kodi_type, path
        or None each time if not found.
        """
        if not self._already_slept:
            # SLEEP before calling this for the first time just after playback
            # start as Kodi updates this info very late!! Might get previous
            # element otherwise
            self._already_slept = True
            self.waitForAbort(1)
        try:
            json_item = js.get_item(playerid)
        except KeyError:
            LOG.debug('No playing item returned by Kodi')
            return None, None, None
        LOG.debug('Kodi playing item properties: %s', json_item)
        return (json_item.get('id'),
                json_item.get('type'),
                json_item.get('file'))

    def PlayBackStart(self, data):
        """
        Called whenever playback is started. Example data:
        {
            u'item': {u'type': u'movie', u'title': u''},
            u'player': {u'playerid': 1, u'speed': 1}
        }
        Unfortunately when using Widgets, Kodi doesn't tell us shit
        """
        self._already_slept = False
        skip_plex_markers.reset_runtime(clear_properties=True)
        # Get the type of media we're playing
        try:
            playerid = data['player']['playerid']
            kodi_id = data['item'].get('id')
            kodi_type = data['item'].get('type')
            path = data['item'].get('file')
        except (TypeError, KeyError):
            LOG.info('Aborting playback report - item invalid for updates %s',
                     data)
            return
        if data['item'].get('channeltype') == 'tv':
            LOG.info('TV playback detected, aborting Plex playback report')
            return
        if playerid == -1:
            # Kodi might return -1 for "last player"
            # Getting the playerid is really a PITA
            try:
                playerid = js.get_player_ids()[0]
            except IndexError:
                # E.g. Kodi 18 doesn't tell us anything useful
                if kodi_type in v.KODI_VIDEOTYPES:
                    playlist_type = v.KODI_TYPE_VIDEO_PLAYLIST
                elif kodi_type in v.KODI_AUDIOTYPES:
                    playlist_type = v.KODI_TYPE_AUDIO_PLAYLIST
                else:
                    LOG.error('Unexpected type %s, data %s', kodi_type, data)
                    return
                playerid = js.get_playlist_id(playlist_type)
                if not playerid:
                    LOG.error('Coud not get playerid for data %s', data)
                    return
        playqueue = app.PLAYQUEUES[playerid]
        info = js.get_player_props(playerid)
        if playqueue.kodi_playlist_playback:
            # Kodi will tell us the wrong position - of the playlist, not the
            # playqueue, when user starts playing from a playlist :-(
            pos = 0
            LOG.debug('Detected playback from a Kodi playlist')
        else:
            pos = info['position'] if info['position'] != -1 else 0
            LOG.debug('Detected position %s for %s', pos, playqueue)
        status = app.PLAYSTATE.player_states[playerid]
        try:
            item = playqueue.items[pos]
            LOG.debug('PKC playqueue item is: %s', item)
        except IndexError:
            # PKC playqueue not yet initialized
            LOG.debug('Position %s not in PKC playqueue yet', pos)
            initialize = True
        else:
            if not kodi_id:
                kodi_id, kodi_type, path = self._json_item(playerid)
            if kodi_id and item.kodi_id:
                if item.kodi_id != kodi_id or item.kodi_type != kodi_type:
                    LOG.debug('Detected different Kodi id')
                    initialize = True
                else:
                    initialize = False
            else:
                # E.g. clips set-up previously with no Kodi DB entry
                if not path:
                    kodi_id, kodi_type, path = self._json_item(playerid)
                if path == '':
                    LOG.debug('Detected empty path: aborting playback report')
                    return
                if item.file != path:
                    # Clips will get a new path
                    LOG.debug('Detected different path')
                    try:
                        tmp_plex_id = int(utils.REGEX_PLEX_ID.findall(path)[0])
                    except (IndexError, TypeError):
                        LOG.debug('No Plex id in path, need to init playqueue')
                        initialize = True
                    else:
                        if tmp_plex_id == item.plex_id:
                            LOG.debug('Detected different path for the same id')
                            initialize = False
                        else:
                            LOG.debug('Different Plex id, need to init playqueue')
                            initialize = True
                else:
                    initialize = False
        if initialize:
            LOG.debug('Need to initialize Plex and PKC playqueue')
            if not kodi_id or not kodi_type or not path:
                kodi_id, kodi_type, path = self._json_item(playerid)
            plex_id, plex_type = self._get_ids(kodi_id, kodi_type, path)
            if not plex_id:
                LOG.debug('No Plex id obtained - aborting playback report')
                app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
                return
            try:
                item = PL.init_plex_playqueue(playqueue, plex_id=plex_id)
            except exceptions.PlaylistError:
                LOG.info('Could not initialize the Plex playlist')
                return
            item.file = path
            # Set the Plex container key (e.g. using the Plex playqueue)
            container_key = None
            if info['playlistid'] != -1:
                # -1 is Kodi's answer if there is no playlist
                container_key = app.PLAYQUEUES[playerid].id
            if container_key is not None:
                container_key = '/playQueues/%s' % container_key
            elif plex_id is not None:
                container_key = '/library/metadata/%s' % plex_id
        else:
            LOG.debug('No need to initialize playqueues')
            kodi_id = item.kodi_id
            kodi_type = item.kodi_type
            plex_id = item.plex_id
            plex_type = item.plex_type
            path = item.file
            if playqueue.id:
                container_key = '/playQueues/%s' % playqueue.id
            else:
                container_key = '/library/metadata/%s' % plex_id
        if plex_type == v.PLEX_TYPE_EPISODE and \
                xbmc.getCondVisibility('System.AddonIsEnabled(service.upnext)'):
            # Let's use Up Next if the add-on is activated REGARDLESS of
            # skipping credits is enabled or not
            upnext_integration = True
        else:
            upnext_integration = False
        # Mechanik for Plex skip intro/credits/commercials feature
        if utils.settings('enableSkipIntro') == 'true' \
                or utils.settings('enableSkipCredits') == 'true' \
                or utils.settings('enableSkipCommercials') == 'true':
            status['markers'] = item.api.markers()
            status['markers_hidden'] = {}
            status['first_credits_marker'] = item.api.first_credits_marker()
            status['final_credits_marker'] = item.api.final_credits_marker()
        # Set credits markers if skip credits OR Up Next is enabled
        # Up Next uses these markers for notification timing
        elif upnext_integration:
            status['first_credits_marker'] = item.api.first_credits_marker()
            status['final_credits_marker'] = item.api.final_credits_marker()

        if item.playmethod is None and path and not path.startswith('plugin://'):
            item.playmethod = v.PLAYBACK_METHOD_DIRECT_PATH
        item.playerid = playerid
        playback_generation = getattr(
            app.PLAYSTATE, 'playback_generation', 0) + 1
        item.pkc_playback_generation = playback_generation
        _activate_upnext_handoff(item)
        # Remember the currently playing item
        app.PLAYSTATE.item = item
        app.PLAYSTATE.playback_generation = playback_generation
        # Remember that this player has been active
        app.PLAYSTATE.active_players.add(playerid)
        status.update(info)
        LOG.debug('Set the Plex container_key to: %s', container_key)
        status['container_key'] = container_key
        status['file'] = path
        status['kodi_id'] = kodi_id
        status['kodi_type'] = kodi_type
        status['plex_id'] = plex_id
        status['plex_type'] = plex_type
        status['playmethod'] = item.playmethod
        status['playcount'] = item.playcount
        status['external_player'] = app.APP.player.isExternalPlayer() == 1
        LOG.debug('Set the player state: %s', status)

        if playerid == v.KODI_VIDEO_PLAYER_ID:
            task = InitVideoStreams(item)
            backgroundthread.BGThreader.addTask(task)
            # Send Up Next signal for episodes if enabled
            if upnext_integration:
                task = SendUpNextSignal(item, status)
                backgroundthread.BGThreader.addTask(task)

    def _on_av_change(self, data):
        """
        Will be called when Kodi has a video, audio or subtitle stream. Also
        happens when the stream changes.

        Example data as returned by Kodi:
            {'item': {'id': 5, 'type': 'movie'},
             'player': {'playerid': 1, 'speed': 1}}
        """
        pass


def _playback_cleanup(ended=False):
    """
    PKC cleanup after playback ends/is stopped. Pass ended=True if Kodi
    completely finished playing an item (because we will get and use wrong
    timing data otherwise)
    """
    LOG.debug('playback_cleanup called. Active players: %s',
              app.PLAYSTATE.active_players)
    if app.APP.skip_markers_dialog:
        app.APP.skip_markers_dialog.close()
        app.APP.skip_markers_dialog = None
    skip_plex_markers.reset_runtime(clear_properties=True)
    # We might have saved a transient token from a user flinging media via
    # Companion (if we could not use the playqueue to store the token)
    app.CONN.plex_transient_token = None
    for playerid in app.PLAYSTATE.active_players:
        status = app.PLAYSTATE.player_states[playerid]
        # Stop transcoding
        if status['playmethod'] == v.PLAYBACK_METHOD_TRANSCODE:
            LOG.debug('Tell the PMS to stop transcoding')
            backgroundthread.BGThreader.addTask(
                backgroundthread.FunctionAsTask(
                    DU().downloadUrl, None,
                    '{server}/video/:/transcode/universal/stop',
                    parameters={'session': v.PKC_MACHINE_IDENTIFIER}))
        if playerid == 1:
            # Bookmarks might not be pickup up correctly, so let's do them
            # manually. Applies to addon paths, but direct paths might have
            # started playback via PMS
            _record_playstate(status, ended)
        # Reset the player's status
        app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
    # As all playback has halted, reset the players that have been active
    app.PLAYSTATE.active_players = set()
    app.PLAYSTATE.item = None
    utils.delete_temporary_subtitles()
    backgroundthread.BGThreader.addTask(RecoverStrandedPlaybackWindow())
    LOG.debug('Finished PKC playback cleanup')


def _current_kodi_window():
    try:
        result = js.JsonRPC('GUI.GetProperties').execute(
            {'properties': ['currentwindow']})
    except Exception:
        LOG.debug('Could not query Kodi window for playback recovery',
                  exc_info=True)
        return {}
    try:
        return result['result']['currentwindow'] or {}
    except (KeyError, TypeError):
        return {}


def _is_stranded_playback_window(window):
    try:
        window_id = int(window.get('id'))
    except (TypeError, ValueError):
        window_id = None
    if window_id in STRANDED_PLAYBACK_WINDOW_IDS:
        return True
    return window.get('label') in STRANDED_PLAYBACK_WINDOW_LABELS


class RecoverStrandedPlaybackWindow(backgroundthread.Task):
    """
    Kodi can leave fullscreen playback windows active after stopping a video
    whose startup never fully completed. Recover only after Kodi confirms there
    is no active player, so normal playback UI is left alone.
    """
    def __init__(self, delay=STRANDED_PLAYBACK_WINDOW_RECOVERY_DELAY):
        self.delay = delay
        super(RecoverStrandedPlaybackWindow, self).__init__()

    def run(self):
        if self.delay and app.APP.monitor.waitForAbort(self.delay):
            return
        try:
            players = js.get_players()
        except Exception:
            LOG.debug('Could not query active Kodi players for playback '
                      'window recovery', exc_info=True)
            return
        if players:
            return
        window = _current_kodi_window()
        if not _is_stranded_playback_window(window):
            return
        LOG.warning('Recovering stranded Kodi playback window after playback '
                    'stopped: %s', window)
        for command in STRANDED_PLAYBACK_WINDOW_RECOVERY_COMMANDS:
            xbmc.executebuiltin(command)


def _record_playstate(status, ended):
    if not status['plex_id']:
        LOG.debug('No Plex id found to record playstate for status %s', status)
        return
    if status['plex_type'] not in v.PLEX_VIDEOTYPES:
        LOG.debug('Not messing with non-video entries')
        return
    with PlexDB(lock=False) as plexdb:
        db_item = plexdb.item_by_id(status['plex_id'], status['plex_type'])
    if not db_item:
        # Item not (yet) in Kodi library
        LOG.debug('No playstate update due to Plex id not found: %s', status)
        return
    time, totaltime, playcount, last_played = _playback_progress(
        status, ended, db_item)
    with kodi_db.KodiVideoDB() as kodidb:
        kodidb.set_resume(db_item['kodi_fileid'],
                          time,
                          totaltime,
                          playcount,
                          last_played)
        if 'kodi_fileid_2' in db_item and db_item['kodi_fileid_2']:
            # Dirty hack for our episodes
            kodidb.set_resume(db_item['kodi_fileid_2'],
                              time,
                              totaltime,
                              playcount,
                              last_played)
    xbmc.executebuiltin('Container.Refresh')
    if status['plex_type'] == v.PLEX_TYPE_EPISODE:
        gui_refresh.refresh_sidepanel_containers((
            (status['plex_id'], status['plex_type']),
        ))
    task = backgroundthread.FunctionAsTask(_clean_file_table, None)
    backgroundthread.BGThreader.addTasksToFront([task])


def _playback_progress(status, ended, db_item):
    LOG.debug('First credits marker: %s', status['first_credits_marker'])
    LOG.debug('Last credits marker: %s', status['final_credits_marker'])
    LOG.debug('Using PMS setting LibraryVideoPlayedAtBehaviour=%s',
              v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR)
    LOG.debug('Kodi advancedsettings: playcountminimumpercent=%s, '
              'ignoresecondsatstart=%s, '
              'ignorepercentatend=%s',
              v.KODI_PLAYCOUNTMINIMUMPERCENT,
              v.KODI_IGNORESECONDSATSTART,
              v.KODI_IGNOREPERCENTATEND)
    totaltime = float(timing.kodi_time_to_millis(status['totaltime'])) / 1000
    # Safety net should we ever get 0
    totaltime = totaltime or 0.000001
    last_played = timing.kodi_now()
    playcount = status['playcount']
    if playcount is None:
        LOG.debug('playcount not found, looking it up in the Kodi DB')
        with kodi_db.KodiVideoDB(lock=False) as kodidb:
            playcount = kodidb.get_playcount(db_item['kodi_fileid']) or 0
    if status['external_player']:
        # video has either been entirely watched - or not.
        # "ended" won't work, need a workaround
        ended = _external_player_correct_plex_watch_count(db_item)
        time = 0.0
        progress = 0.0
    else:
        time = float(timing.kodi_time_to_millis(status['time'])) / 1000
        progress = time / totaltime
        LOG.debug('time %s, totaltime %s, progress %s, MARK_PLAYED_AT %s',
                  time, totaltime, progress, v.MARK_PLAYED_AT)
        # If there is no first credits marker, use the last credits
        first = status['first_credits_marker'] or status['final_credits_marker']
        last = status['final_credits_marker']
        # Decide on whether video ended - based on the PMS setting
        if v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR == 3 \
                and first \
                and first[0] / totaltime < v.MARK_PLAYED_AT:
            # "earliest between threshold percent and first credits marker"
            ended = True if time >= first[0] else False
        elif v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR == 1 and last:
            # "at final credits marker position"
            ended = True if time >= last[0] else False
        elif v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR == 2 and first:
            # "at first credits marker position"
            ended = True if time >= first[0] else False
        else:
            # use threshold, corresponds to
            # v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR = 0
            ended = True if progress >= v.MARK_PLAYED_AT else False
        LOG.debug('Deduced that video has ended: %s', ended)
    if ended:
        playcount += 1
        time = 0.0
        progress = 100.0
    elif not status['external_player'] and time < v.IGNORE_SECONDS_AT_START:
        LOG.debug('Ignoring playback less than %s seconds',
                  v.IGNORE_SECONDS_AT_START)
        # Annoying Plex bug - it'll reset an already watched video to unwatched
        playcount = None
        last_played = None
        time = 0.0
        progress = 0.0
    LOG.debug('Resulting playback progress %s (%s of %s seconds) playcount %s',
              progress, time, totaltime, playcount)
    return time, totaltime, playcount, last_played


def _external_player_correct_plex_watch_count(db_item):
    """
    Kodi won't safe playstate at all for external players

    There's currently no way to get a resumpoint if an external player is
    in use  We are just checking whether we should mark video as
    completely watched or completely unwatched (according to
    playcountminimumtime set in playercorefactory.xml)
    See https://kodi.wiki/view/External_players
    """
    with kodi_db.KodiVideoDB(lock=False) as kodidb:
        playcount = kodidb.get_playcount(db_item['kodi_fileid'])
    LOG.debug('External player detected. Playcount: %s', playcount)
    _queue_pms_playstate(
        db_item['plex_id'], 'watched' if playcount else 'unwatched')
    return True if playcount else False


def _clean_file_table():
    """
    If we associate a playing video e.g. pointing to plugin://... to an existing
    Kodi library item, Kodi will add an additional entry for this (additional)
    path plugin:// in the file table. This leads to all sorts of wierd behavior.
    This function tries for at most 5 seconds to clean the file table.
    """
    LOG.debug('Start cleaning Kodi files table')
    if app.APP.monitor.waitForAbort(2):
        # PKC should exit
        return
    try:
        with kodi_db.KodiVideoDB() as kodidb:
            obsolete_file_ids = list(kodidb.obsolete_file_ids())
            for file_id in obsolete_file_ids:
                LOG.debug('Removing obsolete Kodi file_id %s', file_id)
                kodidb.remove_file(file_id, remove_orphans=False)
    except utils.OperationalError:
        LOG.debug('Database was locked, unable to clean file table')
    else:
        LOG.debug('Done cleaning up Kodi file table')


def _next_episode(current_api):
    """
    Returns the xml for the next episode after the current one
    Returns None if something went wrong or there is no next episode
    """
    xml = PF.show_episodes(current_api.grandparent_id())
    if xml is None:
        return
    for counter, episode in enumerate(xml):
        api = API(episode)
        if api.plex_id == current_api.plex_id:
            break
    else:
        LOG.error('Did not find the episode with Plex id %s for show %s: %s',
                  current_api.plex_id, current_api.grandparent_id(),
                  current_api.grandparent_title())
        return
    try:
        return API(xml[counter + 1])
    except IndexError:
        # Was the last episode
        pass


def _complete_artwork_keys(info):
    """
    Make sure that the minimum set of keys is present in the info dict
    """
    for key in ('tvshow.poster',
                'tvshow.fanart',
                'tvshow.landscape',
                'tvshow.clearart',
                'tvshow.clearlogo',
                'thumb'):
        if key not in info['art']:
            info['art'][key] = ''


class RestorePlexPlaystate(backgroundthread.Task):
    """Restore PMS-authoritative playstate after a delayed Kodi handoff update."""
    def __init__(self, kodi_id, kodi_type, handoff_key=None):
        self.kodi_id = kodi_id
        self.kodi_type = kodi_type
        self.handoff_key = handoff_key
        super(RestorePlexPlaystate, self).__init__()

    def _restore_once(self, revision=None):
        item = (self.kodi_id, self.kodi_type)
        if revision is not None and \
                _playback_restore_revision(item, self.handoff_key) != revision:
            return False
        with PlexDB() as plexdb:
            db_item = plexdb.item_by_kodi_id(
                self.kodi_id, self.kodi_type)
        if not db_item:
            LOG.error('Could not restore delayed playback update for '
                      'Kodi item %s/%s: Plex mapping not found',
                      self.kodi_type, self.kodi_id)
            return False
        xml = PF.GetPlexMetadata(db_item['plex_id'])
        if not xml or xml == 401:
            LOG.error('Could not restore delayed playback update for '
                      'Plex item %s: metadata unavailable',
                      db_item['plex_id'])
            return False
        api = API(xml[0])
        resume = api.resume_point()
        runtime = api.runtime()
        playcount = api.viewcount()
        lastplayed = api.lastplayed()
        # A completion callback can arrive while the PMS metadata request is
        # in flight. Serialize the final validity check with the Kodi writes
        # so a canceled restore cannot overwrite the completed state.
        with _PLAYBACK_RESTORE_LOCK:
            if revision is not None:
                state = _PENDING_PLAYBACK_RESTORES.get(item)
                if state is None or \
                        state.get('handoff_key') != self.handoff_key or \
                        state.get('revision') != revision:
                    return False
                # A reset callback increments the revision and should get its
                # follow-up restore; a completed or explicit unwatch callback
                # removes the state as terminal cancellation.
            with kodi_db.KodiVideoDB() as kodidb:
                kodidb.set_resume(db_item['kodi_fileid'],
                                  resume,
                                  runtime,
                                  playcount,
                                  lastplayed)
                if db_item.get('kodi_fileid_2'):
                    kodidb.set_resume(db_item['kodi_fileid_2'],
                                      resume,
                                      runtime,
                                      playcount,
                                      lastplayed)
        xbmc.executebuiltin('Container.Refresh')
        if db_item['plex_type'] == v.PLEX_TYPE_EPISODE:
            gui_refresh.refresh_sidepanel_containers((
                (db_item['plex_id'], db_item['plex_type']),
            ))
        LOG.info('Restored PMS playstate after delayed Kodi update for '
                 'Plex item %s', db_item['plex_id'])
        return True

    def run(self):
        item = (self.kodi_id, self.kodi_type)
        failures = 0
        last_attempted_revision = None
        aborted = False
        for _ in range(PLAYBACK_RESTORE_MAX_PASSES):
            if app.APP.monitor.waitForAbort(PLAYBACK_RESTORE_QUIET_SECONDS):
                aborted = True
                break
            revision = _playback_restore_revision(item, self.handoff_key)
            if revision is None:
                return
            last_attempted_revision = revision
            try:
                restored = self._restore_once(revision)
            except Exception:
                restored = False
                LOG.exception('Could not restore PMS playstate after delayed '
                              'Kodi update for item %s/%s',
                              self.kodi_type, self.kodi_id)
            if restored:
                failures = 0
                if _finish_playback_restore(
                        item, revision, self.handoff_key):
                    return
                # A callback arrived while the restore was in flight. Wait for
                # its quiet period, then repair the authoritative final state.
                continue
            failures += 1
            if failures >= PLAYBACK_RESTORE_MAX_ATTEMPTS:
                break
            if app.APP.monitor.waitForAbort(PLAYBACK_RESTORE_RETRY_SECONDS):
                aborted = True
                break
        if aborted:
            _release_playback_restore(item, self.handoff_key)
            return
        if _requeue_playback_restore_if_changed(
                item, self.handoff_key, last_attempted_revision):
            backgroundthread.BGThreader.addTask(
                RestorePlexPlaystate(
                    self.kodi_id, self.kodi_type, self.handoff_key))


def _videolibrary_onupdate(data):
    """
    A specific Kodi library item has been updated. This seems to happen if the
    user marks an item as watched/unwatched or if playback of the item just
    stopped

    2 kinds of messages possible, e.g.
        Method: VideoLibrary.OnUpdate Data: ("Reset resume position" and also
        fired just after stopping playback - BEFORE OnStop fires)
            {'id': 1, 'type': 'movie'}
        Method: VideoLibrary.OnUpdate Data: ("Mark as watched")
            {'item': {'id': 1, 'type': 'movie'}, 'playcount': 1}
    """
    global _ACTIVE_UPNEXT_HANDOFF
    item = data.get('item') if 'item' in data else data
    try:
        kodi_id = item['id']
        kodi_type = item['type']
    except (KeyError, TypeError):
        LOG.debug("Item is invalid for a Plex playstate update")
        return
    handoff = _upnext_handoff_for_update(kodi_id, kodi_type)
    handoff_key = _upnext_handoff_key(handoff) if handoff else None
    now = monotonic()
    resume_present = False
    playcount_provided = data.get('playcount') is not None
    playcount = data.get('playcount')
    if playcount is None:
        # "Reset resume position"
        # Kodi might set as watched or unwatched!
        with KodiVideoDB(lock=False) as kodidb:
            file_id = kodidb.file_id_from_id(kodi_id, kodi_type)
            if file_id is None:
                return
            resume_present = bool(kodidb.get_resume(file_id))
            playcount = kodidb.get_playcount(file_id) or 0
    manual_unwatched_handoff = playcount_provided and playcount <= 0 and \
        handoff is not None
    if manual_unwatched_handoff:
        _cancel_playback_restore(
            (kodi_id, kodi_type), handoff_key)
        _COMPLETED_UPNEXT_HANDOFFS.pop((kodi_id, kodi_type), None)
        if handoff is _ACTIVE_UPNEXT_HANDOFF:
            _ACTIVE_UPNEXT_HANDOFF = None
        expected = getattr(app.PLAYSTATE, 'expected_upnext_handoff', None)
        if handoff is expected or (
                expected is not None and
                _upnext_handoff_key(expected) == handoff_key):
            app.PLAYSTATE.expected_upnext_handoff = None
            app.PLAYSTATE.started_upnext_handoff = None
        _force_kodi_unwatched(kodi_id, kodi_type)
        handoff = None
        handoff_key = None
    elif resume_present:
        if handoff is not None and _upnext_watched_reported(handoff):
            handoff['reset_seen'] = True
            _force_kodi_watched(handoff, kodi_id, kodi_type, handoff_key)
        # An existing bookmark is normally a resume update, not a watched
        # toggle. A completed handoff is the one exception: it must win over
        # the stale bookmark and leave Kodi watched with no resume point.
        return
    completed_handoff = False
    if playcount > 0 and handoff is not None:
        completion = _claim_upnext_watched(
            handoff, kodi_id, kodi_type, now)
        if completion is False:
            _force_kodi_watched(handoff, kodi_id, kodi_type, handoff_key)
            LOG.debug('Ignoring duplicate completed Up Next update for '
                      'Kodi item %s/%s', kodi_type, kodi_id)
            return
        completed_handoff = True
    elif handoff is None and not manual_unwatched_handoff and \
            app.PLAYSTATE.item and \
            kodi_id == app.PLAYSTATE.item.kodi_id and \
            kodi_type == app.PLAYSTATE.item.kodi_type:
        # Kodi updates an item immediately after playback. Hence we do NOT
        # increase or decrease the viewcount
        return
    if handoff is not None and not completed_handoff:
        if _upnext_watched_reported(handoff):
            handoff['reset_seen'] = True
            _force_kodi_watched(
                handoff, kodi_id, kodi_type, handoff_key)
            LOG.debug('Ignoring delayed reset after completed Up Next update '
                      'for Kodi item %s/%s', kodi_type, kodi_id)
            return
        LOG.debug('Ignoring delayed playback update for Kodi item %s/%s',
                  kodi_type, kodi_id)
        if _request_playback_restore(
                kodi_id, kodi_type, handoff_key, handoff):
            backgroundthread.BGThreader.addTask(
                RestorePlexPlaystate(kodi_id, kodi_type, handoff_key))
        return
    if completed_handoff:
        _force_kodi_watched(handoff, kodi_id, kodi_type, handoff_key)
    if completed_handoff and handoff.get('previous_plex_id'):
        _queue_pms_playstate(handoff['previous_plex_id'], 'watched')
        return
    # Send notification to the server.
    cancelled_restore = None
    if playcount > 0:
        cancelled_restore = _cancel_playback_restore((kodi_id, kodi_type))
    with PlexDB(lock=False) as plexdb:
        db_item = plexdb.item_by_kodi_id(kodi_id, kodi_type)
    if not db_item:
        LOG.error("Could not find plex_id in plex database for a "
                  "video library update")
        return
    if cancelled_restore:
        _repair_kodi_watched(db_item)
    # notify the server (async — Kodi DB is already updated at this point,
    # PMS sync is pure propagation and must not block the UI thread)
    state = 'watched' if playcount > 0 else 'unwatched'
    _queue_pms_playstate(db_item['plex_id'], state)


class PKCPlayer(xbmc.Player):
    """
    xbmc.Player subclass that hooks onPlayBackError so PKC can fall back from
    a failed direct-path open to HTTP streaming via the PMS addon path.

    When Kodi fails to open a direct-path file (e.g. because an SMB share is
    temporarily unavailable), this retries the same item through the PKC plugin
    endpoint, which streams via HTTP from the Plex Media Server instead.
    """

    def __init__(self):
        self._fallback_in_progress = False
        super().__init__()

    def onAVStarted(self):
        """Stream actually started - clear the fallback guard."""
        self._fallback_in_progress = False

    def onPlayBackError(self):
        """
        Kodi signals that playback failed to open.  If the failing item was a
        direct path (not already a plugin:// URL) attempt one retry via the PKC
        addon path, which will stream via HTTP from the PMS.
        """
        LOG.warning('onPlayBackError: Kodi playback error detected')
        if self._fallback_in_progress:
            # The addon-path retry itself failed - give up and reset.
            LOG.warning('onPlayBackError: addon-path fallback also failed, giving up')
            self._fallback_in_progress = False
            return

        # The Kodi video playlist usually still holds the item after a failed
        # open, so query it to find out what was supposed to play.
        try:
            items = js.playlist_get_items(v.KODI_VIDEO_PLAYER_ID)
        except Exception:
            LOG.debug('onPlayBackError: could not query Kodi playlist')
            return

        if not items:
            LOG.debug('onPlayBackError: playlist is empty, nothing to fall back')
            return

        item = items[0]
        path = item.get('file', '')
        kodi_id = item.get('id')
        kodi_type = item.get('type')

        # Only fall back for direct-path items – plugin:// URLs are already
        # going through PKC and failing for a different reason.
        if not path or path.startswith('plugin://'):
            LOG.debug('onPlayBackError: not a direct path (%s), skipping', path)
            return

        LOG.warning('onPlayBackError: direct path "%s" failed, attempting addon fallback', path)

        # Resolve Kodi id when the playlist item did not include it.
        if not kodi_id and kodi_type:
            kodi_id, _ = kodi_db.kodiid_from_filename(path, kodi_type)

        if not kodi_id or not kodi_type:
            LOG.debug('onPlayBackError: cannot determine kodi_id/kodi_type, skipping')
            return

        with PlexDB(lock=False) as plexdb:
            db_item = plexdb.item_by_kodi_id(kodi_id, kodi_type)

        if not db_item:
            LOG.debug('onPlayBackError: no Plex DB entry for kodi_id=%s type=%s',
                      kodi_id, kodi_type)
            return

        plex_id = db_item['plex_id']
        plex_type = db_item['plex_type']
        LOG.warning('onPlayBackError: retrying plex_id=%s via PKC addon path', plex_id)
        self._fallback_in_progress = True
        xbmc.executebuiltin(
            'RunPlugin(plugin://%s/?mode=play&plex_id=%s&plex_type=%s)'
            % (v.ADDON_ID, plex_id, plex_type))


class InitVideoStreams(backgroundthread.Task):
    """
    The Kodi player takes forever to initialize all streams Especially
    subtitles, apparently. No way to tell when Kodi is done :-(
    """

    def __init__(self, item):
        self.item = item
        super().__init__()

    def run(self):
        if app.APP.monitor.waitForAbort(WAIT_BEFORE_INIT_STREAMS):
            return
        i = 0
        while True:
            try:
                self.item.init_streams()
            except Exception as err:
                i += 1
                if app.APP.monitor.waitForAbort(1):
                    return
                if i > ADDITIONAL_WAIT_BEFORE_INIT_STREAMS:
                    LOG.error('Exception encountered while init streams:')
                    LOG.error(err)
                    return
            else:
                break


class SendUpNextSignal(backgroundthread.Task):
    """
    Sends the Up Next signal after playback has started.
    We wait a bit to ensure playback is fully initialized.
    """

    def __init__(self, item, status):
        self.item = item
        self.status = status
        self.playback_generation = getattr(
            item, 'pkc_playback_generation', None)
        super().__init__()

    def _is_current_playback(self):
        return self.playback_generation is not None and \
            app.PLAYSTATE.item is self.item and \
            getattr(app.PLAYSTATE, 'playback_generation', None) == \
            self.playback_generation and \
            getattr(self.item, 'pkc_playback_generation', None) == \
            self.playback_generation

    def run(self):
        # Wait for playback to stabilize before sending Up Next signal
        if app.APP.monitor.waitForAbort(2):
            return
        try:
            with app.APP.lock_playqueues:
                if not self._is_current_playback():
                    LOG.debug('Discarding stale Up Next task before payload '
                              'preparation')
                    return
            # Get notification time from Plex credits markers if available
            notification_time = upnext.get_notification_time_from_markers(self.status)
            prepared = upnext.prepare_upnext_signal(
                self.item.api, notification_time)
            # Payload preparation may perform a slow PMS lookup. Revalidate the
            # exact playback generation before broadcasting or changing state.
            with app.APP.lock_playqueues:
                if not self._is_current_playback():
                    LOG.debug('Discarding stale Up Next task after payload '
                              'preparation')
                    return
                handoff = upnext.emit_upnext_signal(prepared) \
                    if prepared else False
                signal_sent = bool(handoff)
                # Store whether Up Next found a next episode. If False, PKC
                # skip credits popup will still show for last episodes.
                playerid = self.item.playerid
                if handoff:
                    app.PLAYSTATE.expected_upnext_handoff = {
                        'token': handoff['token'],
                        'previous_kodi_id': self.item.kodi_id,
                        'previous_kodi_type': self.item.kodi_type,
                        'previous_plex_id': self.item.plex_id,
                        'previous_generation': self.playback_generation,
                        'next_plex_id': handoff['next_plex_id'],
                        'created_at': monotonic(),
                    }
                app.PLAYSTATE.player_states[playerid]['upnext_signal_sent'] = signal_sent
                app.PLAYSTATE.player_states[playerid][
                    'upnext_replaces_credit_skip'] = bool(
                        signal_sent and notification_time is not None)
            # The cached credits/Up Next decision may have been evaluated
            # before this delayed task completed.
            skip_plex_markers.reset_runtime()
            if not signal_sent:
                LOG.debug('Up Next: No next episode - PKC skip credits will handle last episode')
        except Exception as err:
            LOG.error('Exception encountered while sending Up Next signal:')
            LOG.error(err)
