#!/usr/bin/env python
# -*- coding: utf-8 -*-
import time
from logging import getLogger

import xbmc

from .windows.skip_marker import SkipMarkerDialog
from . import app, skip_marker_state, utils, variables as v

LOG = getLogger('PLEX.skipmarkers')


# How many seconds before the (adjusted) marker start to begin the auto-skip
# countdown. During this window the dialog shows a live countdown; if the
# user does nothing the skip fires when it reaches zero. Clicking cancels.
AUTO_SKIP_COUNTDOWN_SECONDS = 10.0

# PMS intro/credits markers tend to fire a few seconds early. Shift the
# effective marker start forward by this amount so the skip lands closer
# to the actual intro/credits boundary.
MARKER_START_OFFSET = 2.0

# Supported types of markers that can be skipped; values here will be
# displayed to the user when skipping is available
MARKERS = {
    'intro': (utils.lang(30525), 'enableSkipIntro', 'enableAutoSkipIntro'), # Skip intro
    'credits': (utils.lang(30526), 'enableSkipCredits', 'enableAutoSkipCredits'),  # Skip credits
    'commercial': (utils.lang(30530), 'enableSkipCommercials', 'enableAutoSkipCommercials'),  # Skip commercial
}


def _set_marker_properties(properties):
    for key, value in properties.items():
        utils.setGlobalProperty('skip_marker.%s' % key, value)


def _clear_marker_properties():
    _set_marker_properties(skip_marker_state.clear_properties())


def _auto_hide_seconds():
    if utils.settings("enableAutoHideSkip") != "true":
        return 0
    return int(utils.settings("enableAutoHideSkipTime"))


def _publish_marker_state(marker_type, marker_definition, marker_end, creation_time, progress,
                          toast_visible=True, auto_hide_seconds=None, auto_skip=False):
    _set_marker_properties(skip_marker_state.build_properties(
        marker_type=marker_type,
        marker_message=marker_definition[0],
        marker_end=marker_end,
        creation_time=creation_time,
        progress=progress,
        auto_hide_seconds=auto_hide_seconds if auto_hide_seconds is not None else _auto_hide_seconds(),
        toast_visible=toast_visible,
        auto_skip=auto_skip))


def _creation_walltime(dialog, playback_progress, current_walltime):
    creation_walltime = getattr(dialog, 'creation_walltime', None)
    if creation_walltime is not None:
        return creation_walltime

    creation_progress = getattr(dialog, 'creation_time', None)
    if creation_progress is None:
        return current_walltime
    elapsed_playback = max(0.0, float(playback_progress) - float(creation_progress))
    return current_walltime - elapsed_playback


def _should_skip_credits_popup():
    """
    Returns True if we should suppress the PKC credits popup.
    This happens when Up Next is enabled AND found a next episode.
    If there's no next episode (last episode), we still show PKC credits popup.
    """
    if not xbmc.getCondVisibility('System.AddonIsEnabled(service.upnext)'):
        return False

    # Check if Up Next actually sent a signal (found next episode)
    with app.APP.lock_playqueues:
        if len(app.PLAYSTATE.active_players) != 1:
            return False
        playerid = list(app.PLAYSTATE.active_players)[0]
        player_state = app.PLAYSTATE.player_states.get(playerid, {})
        return player_state.get('upnext_signal_sent', False)

def _effective_start(marker_start):
    """Adjusted marker start time, shifted forward to compensate for PMS
    marker inaccuracy."""
    return marker_start + MARKER_START_OFFSET

def _countdown_start(marker_start):
    """Wall-clock playback position at which the auto-skip countdown begins."""
    return max(0.0, _effective_start(marker_start) - AUTO_SKIP_COUNTDOWN_SECONDS)

def skip_markers(markers, markers_hidden):
    try:
        progress = app.APP.player.getTime()
    except RuntimeError:
        # XBMC is not playing any media file yet
        return False
    within_marker = None
    marker_definition = None
    marker_start = None
    marker_end = None
    is_auto_skip = False
    for start, end, typus, _ in markers:
        marker_definition = MARKERS[typus]
        # Skip the PKC credits popup if Up Next is enabled (Up Next handles it)
        if typus == 'credits' and _should_skip_credits_popup():
            continue
        skip_enabled = utils.settings(marker_definition[1]) == "true"
        if not skip_enabled:
            continue
        auto_skip = utils.settings(marker_definition[2]) == "true"
        # For auto-skip markers, widen the detection window to include the
        # countdown phase (before the adjusted marker start).
        if auto_skip:
            trigger_start = _countdown_start(start)
        else:
            trigger_start = _effective_start(start)
        # The "-1" is important since timestamps/seeks are not exact and we
        # could end up in an endless loop within start & end
        # see https://github.com/croneter/PlexKodiConnect/issues/2002
        if trigger_start <= progress < end - 1:
            within_marker = typus
            marker_start = start
            marker_end = end
            is_auto_skip = auto_skip
            break
        elif typus in markers_hidden:
            # reset the marker when escaping its time window
            # this allows the skip button to show again if you rewind
            del markers_hidden[typus]

    if within_marker is None:
        if app.APP.skip_markers_dialog is not None:
            app.APP.skip_markers_dialog.close()
            app.APP.skip_markers_dialog = None
            _clear_marker_properties()
        else:
            _clear_marker_properties()
        return False

    # ---- We are within a marker window ----
    current_walltime = time.monotonic()

    if within_marker in markers_hidden:
        # The user dismissed or cancelled this marker; keep it available to
        # skins that expose skip controls in the OSD, but don't show toast.
        _publish_marker_state(within_marker, marker_definition, marker_end,
                              marker_start, progress, toast_visible=False,
                              auto_skip=is_auto_skip)
        return False

    # ------------------------------------------------------------------
    # Auto-skip mode: show a countdown, then skip (unless cancelled)
    # ------------------------------------------------------------------
    if is_auto_skip:
        eff_start = _effective_start(marker_start)
        cd_start = _countdown_start(marker_start)

        if progress >= eff_start:
            # Countdown has expired — fire the skip now
            LOG.info('Auto-skipping %s marker, seeking to %s',
                     within_marker, marker_end)
            app.APP.player.seekTime(marker_end)
            if app.APP.skip_markers_dialog is not None:
                app.APP.skip_markers_dialog.close()
                app.APP.skip_markers_dialog = None
            _clear_marker_properties()
            return True

        # Countdown phase — show/manage the dialog
        if app.APP.skip_markers_dialog is None:
            app.APP.skip_markers_dialog = SkipMarkerDialog(
                'script-plex-skip_marker.xml',
                v.ADDON_PATH,
                'default',
                '1080i',
                marker_message=marker_definition[0],
                marker_end=marker_end,
                creation_time=cd_start,
                creation_walltime=current_walltime,
                auto_skip=True)
            app.APP.skip_markers_dialog.show()

        # Check if user cancelled by clicking the dialog
        if getattr(app.APP.skip_markers_dialog, 'on_hold', False):
            markers_hidden[within_marker] = True
            app.APP.skip_markers_dialog.close()
            app.APP.skip_markers_dialog = None
            _publish_marker_state(within_marker, marker_definition, marker_end,
                                  marker_start, progress, toast_visible=False)
            return False

        # Publish live countdown properties (remaining seconds, progress bar)
        _publish_marker_state(within_marker, marker_definition, marker_end,
                              cd_start, progress,
                              auto_hide_seconds=AUTO_SKIP_COUNTDOWN_SECONDS,
                              toast_visible=True, auto_skip=True)
        return True

    # ------------------------------------------------------------------
    # Manual skip mode: show button, auto-hide after timeout (original)
    # ------------------------------------------------------------------
    if app.APP.skip_markers_dialog is None:
        # WARNING: This Dialog only seems to work if called from the main
        # thread. Otherwise, onClick and onAction won't work
        app.APP.skip_markers_dialog = SkipMarkerDialog(
            'script-plex-skip_marker.xml',
            v.ADDON_PATH,
            'default',
            '1080i',
            marker_message=marker_definition[0],
            marker_end=marker_end,
            creation_time=progress,
            creation_walltime=current_walltime)
        app.APP.skip_markers_dialog.show()

    creation_walltime = _creation_walltime(
        app.APP.skip_markers_dialog, progress, current_walltime)
    if _auto_hide_seconds() and \
        (current_walltime - creation_walltime) > _auto_hide_seconds():
        # the dialog has been open for more than X seconds, so close it and
        # mark it as hidden so it won't show up again within the start/end window
        markers_hidden[within_marker] = True
        app.APP.skip_markers_dialog.close()
        app.APP.skip_markers_dialog = None
        _publish_marker_state(within_marker, marker_definition, marker_end,
                              marker_start, progress, toast_visible=False)
        return False
    else:
        _publish_marker_state(within_marker, marker_definition, marker_end,
                              marker_start, progress, toast_visible=True)
        return True


def skip_active_marker():
    try:
        marker_end = float(utils.getGlobalProperty('skip_marker.end'))
    except (TypeError, ValueError):
        return False
    app.APP.player.seekTime(marker_end)
    if app.APP.skip_markers_dialog is not None:
        app.APP.skip_markers_dialog.close()
        app.APP.skip_markers_dialog = None
    return True

def check():
    with app.APP.lock_playqueues:
        if len(app.PLAYSTATE.active_players) != 1:
            return False
        playerid = list(app.PLAYSTATE.active_players)[0]
        markers = app.PLAYSTATE.player_states[playerid]['markers']
        markers_hidden = app.PLAYSTATE.player_states[playerid]['markers_hidden']
    if not markers:
        return False
    return skip_markers(markers, markers_hidden)
