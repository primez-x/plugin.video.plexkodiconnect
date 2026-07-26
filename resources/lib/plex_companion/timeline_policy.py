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
