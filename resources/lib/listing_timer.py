#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Opt-in timing log for PKC listings (widgets and browsing).

With the setting "Log listing times" (Advanced) on, the PKC service sets the
home window property WINDOW_PROPERTY and every listing writes one LOGINFO
line, e.g.

    PLEX.timing: Listing mode=discover_hub hub_id=trending: 812 ms, 20 items
    (import 310, cache 4, init 9, match 41, details 0, build 380, add 22,
    other 46)

Phases are recorded with mark() whether the log is on or not (a clock read
and a list append each); only report() checks the window property. "import"
counts from the start of default.py until the listing code is imported, not
Kodi's Python interpreter start-up before it.
"""
from logging import getLogger
from time import perf_counter

LOG = getLogger('PLEX.timing')

SETTING = 'listingTimingLog'
WINDOW_PROPERTY = 'plex_listing_timing_log'
# Plugin parameters that identify a listing in the log line
LABEL_PARAMS = ('mode', 'hub_id', 'key', 'section_id', 'content_type',
                'plex_type')

_STATE = {'start': None, 'last': None, 'items': None}
_PHASES = []


def start(started_at=None):
    """Begin timing a listing; started_at is a time.perf_counter() value"""
    now = perf_counter() if started_at is None else started_at
    _STATE['start'] = now
    _STATE['last'] = now
    _STATE['items'] = None
    del _PHASES[:]


def mark(phase):
    """Close the phase that ran since the previous mark() (or start())"""
    if _STATE['start'] is None:
        return
    now = perf_counter()
    _PHASES.append((phase, now - _STATE['last']))
    _STATE['last'] = now


def items(count):
    """Remember the number of items the listing returned"""
    _STATE['items'] = count


def enabled():
    from . import utils
    return utils.window(WINDOW_PROPERTY) == 'true'


def label(params):
    return ' '.join('%s=%s' % (key, params[key]) for key in LABEL_PARAMS
                    if params.get(key))


def format_line(listing, total, phases, count=None):
    """Returns the log line; total and phase durations in seconds"""
    summed = {}
    for phase, duration in phases:
        summed[phase] = summed.get(phase, 0.0) + duration
    other = total - sum(summed.values())
    if other >= 0.0005:
        summed['other'] = other
    parts = ', '.join('%s %d' % (phase, round(duration * 1000))
                      for phase, duration in summed.items())
    line = 'Listing %s: %d ms' % (listing or '(main menu)',
                                  round(total * 1000))
    if count is not None:
        line += ', %d items' % count
    if parts:
        line += ' (%s)' % parts
    return line


def report(listing):
    """Log the listing's timings if the timing log is on. Never raises."""
    try:
        if _STATE['start'] is None or not enabled():
            return None
        line = format_line(listing,
                           perf_counter() - _STATE['start'],
                           _PHASES,
                           _STATE['items'])
        LOG.info(line)
        return line
    except Exception:
        return None


def sync_window_property():
    """PKC service: mirror the setting to the home window property"""
    from . import utils
    if utils.settings(SETTING) == 'true':
        utils.window(WINDOW_PROPERTY, value='true')
    else:
        utils.window(WINDOW_PROPERTY, clear=True)
