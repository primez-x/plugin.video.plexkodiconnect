#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Policy helpers for reporting Kodi playback timelines to Plex."""


class PauseTimelineState(object):
    """Track only successfully reported paused timelines for one player."""

    def __init__(self):
        self.paused_rating_key = None

    def suppresses(self, current):
        return should_suppress_paused_timeline(self.paused_rating_key, current)

    def observe(self, current):
        """Re-arm pauses as soon as Kodi exposes any non-paused state."""
        if current.get('state') == 'paused':
            return None
        previous = self.paused_rating_key
        self.paused_rating_key = None
        return previous

    def record_success(self, current):
        """Only a successful paused request earns future suppression."""
        if current.get('state') == 'paused':
            self.paused_rating_key = current.get('ratingKey')


def should_send_pms_timeline(previous, current):
    """Return False only for repeated pause reports for the same Plex item.

    Plex needs one paused update to save the current position. Repeating the
    same paused state indefinitely lets an unattended device keep overwriting
    progress made elsewhere. A new item, resume, or stop must always be sent.
    """
    paused_rating_key = (
        previous.get('ratingKey')
        if previous.get('state') == 'paused'
        else None
    )
    return not should_suppress_paused_timeline(paused_rating_key, current)


def should_suppress_paused_timeline(paused_rating_key, current):
    """Return whether a successful pause for this item still owns suppression.

    ``paused_rating_key`` is deliberately independent from the last successful
    timeline payload.  A resumed playing request can fail after Plex expires a
    long-paused session; the next pause still needs to be sent rather than
    being hidden behind the old successful paused request.
    """
    rating_key = current.get('ratingKey')
    return (
        current.get('state') == 'paused'
        and rating_key is not None
        and rating_key == paused_rating_key
    )


# Without Companion subscribers, report steady playback to the PMS this often
# [seconds] - the cadence of Plex's own clients. Play/pause/stop, item
# changes and seeks are still reported on the next 1s tick.
PMS_PLAYING_TIMELINE_INTERVAL = 10.0
# Player time drifting this far [ms] from the wall-clock expectation since
# the last report is treated as a seek
PMS_TIMELINE_SEEK_TOLERANCE_MS = 3000
# Timeline fields identifying what is playing; any change is reported
PMS_TIMELINE_IDENTITY_FIELDS = (
    'ratingKey', 'key', 'containerKey', 'playQueueID', 'playQueueItemID',
)


class PlayingTimelineThrottle(object):
    """Rate-limit steady 'playing' PMS timelines for one player."""

    def __init__(self, interval=PMS_PLAYING_TIMELINE_INTERVAL,
                 seek_tolerance_ms=PMS_TIMELINE_SEEK_TOLERANCE_MS):
        self.interval = interval
        self.seek_tolerance_ms = seek_tolerance_ms
        self.last_sent_at = None
        self.last_sent = None

    def due(self, current, now, frequent=False):
        """
        Return True if this timeline must be sent now. Only an unchanged,
        steadily progressing 'playing' timeline within the interval since the
        last successful report is held back. frequent=True (Companion
        subscribers are listening) never holds anything back.
        """
        if frequent or current.get('state') != 'playing':
            return True
        previous = self.last_sent
        if previous is None or previous.get('state') != 'playing':
            return True
        for field in PMS_TIMELINE_IDENTITY_FIELDS:
            if previous.get(field) != current.get(field):
                return True
        elapsed = now - self.last_sent_at
        if elapsed < 0 or elapsed >= self.interval:
            return True
        try:
            expected = int(previous['time']) + elapsed * 1000
            actual = int(current['time'])
        except (KeyError, TypeError, ValueError):
            return True
        return abs(actual - expected) > self.seek_tolerance_ms

    def record_success(self, current, now):
        self.last_sent_at = now
        self.last_sent = dict(current)

    def reset(self):
        self.last_sent_at = None
        self.last_sent = None


# While nothing is playing, re-read the Kodi playlists only this often
# [seconds] unless KodiMonitor saw a playlist change (Playlist.OnAdd etc.)
IDLE_PLAYLIST_POLL_INTERVAL = 5.0


def playlist_poll_due(changed, idle, last_poll, now,
                      idle_interval=IDLE_PLAYLIST_POLL_INTERVAL):
    """
    Whether the Companion PlaystateMgr should compare Kodi's playlists now.

    changed is the threading.Event KodiMonitor sets on playlist and player
    notifications (None if unavailable). During playback the playlists are
    compared on every tick as before; while idle only after a notification,
    with a slow fallback in case one was missed. A set event is cleared here,
    before the playlists are read, so a change during the read is not lost.
    """
    if changed is not None and changed.is_set():
        changed.clear()
        return True
    if not idle or changed is None or last_poll is None:
        return True
    return now - last_poll >= idle_interval
