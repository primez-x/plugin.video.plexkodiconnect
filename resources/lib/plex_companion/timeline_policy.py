#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Policy helpers for reporting Kodi playback timelines to Plex."""


def should_send_pms_timeline(previous, current):
    """Return False only for repeated pause reports for the same Plex item.

    Plex needs one paused update to save the current position. Repeating the
    same paused state indefinitely lets an unattended device keep overwriting
    progress made elsewhere. A new item, resume, or stop must always be sent.
    """
    return not (
        current.get('state') == 'paused'
        and previous.get('state') == 'paused'
        and current.get('ratingKey') == previous.get('ratingKey')
    )
