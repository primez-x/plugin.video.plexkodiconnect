#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Kodi holds a single playstate (playcount, last played, resume point) per file,
but a multi-episode file (e.g. S01E03E04) is one Plex item per episode, each
with its own playstate. Everything writing the playstate of such a file to the
Kodi DB must write the merge of the playstates of all its episodes (see
merged()), or it would overwrite what was done with the other episode.

The playstates of the other episodes come from the PMS. Fetch them with
prefetch() or prefetch_all_shared() BEFORE taking the Plex and Kodi DB locks:
merged() only reads what was fetched, so no network request happens while DB
locks are held.

A playstate is a tuple (resume, runtime, playcount, lastplayed), the arguments
of KodiVideoDB.set_resume()
"""
from logging import getLogger
from threading import Lock
from time import monotonic

from .plex_api import API
from .plex_db import PlexDB
from . import plex_functions as PF, variables as v

LOG = getLogger('PLEX.playstate')

# Seconds a fetched playstate is reused, e.g. for the frequent progress
# messages of a playing session
CACHE_SECONDS = 60

_STATES = {}  # {plex_id: (monotonic fetch time, playstate)}
_LOCK = Lock()


def merge_playstates(playstates):
    """
    Merges {plex_id: playstate} of all episodes sharing one Kodi file into the
    playstate Kodi can hold for it: highest playcount, latest lastplayed and
    the resume point of the episode viewed most recently. The result does not
    depend on the order of the episodes
    """
    plex_id = max(playstates,
                  key=lambda x: (playstates[x][3] or '', x))
    resume, runtime, _, lastplayed = playstates[plex_id]
    return (resume,
            runtime,
            max(x[2] or 0 for x in playstates.values()),
            lastplayed)


def clear():
    with _LOCK:
        _STATES.clear()


def fetch_states(plex_ids, max_age=CACHE_SECONDS):
    """
    Fetches the playstates of the Plex items plex_ids from the PMS unless they
    were fetched less than max_age seconds ago. Returns False if some of them
    could not be fetched. Do not call this while holding DB locks
    """
    success = True
    for plex_id in plex_ids:
        with _LOCK:
            cached = _STATES.get(plex_id)
        if cached and monotonic() - cached[0] <= max_age:
            continue
        xml = PF.GetPlexMetadata(plex_id)
        if xml in (None, 401) or not len(xml):
            LOG.warning('Could not get the playstate of Plex item %s', plex_id)
            with _LOCK:
                _STATES.pop(plex_id, None)
            success = False
            continue
        api = API(xml[0])
        with _LOCK:
            _STATES[plex_id] = (monotonic(), (api.resume_point(),
                                              api.runtime(),
                                              api.viewcount(),
                                              api.lastplayed()))
    return success


def prefetch(plex_id, plex_type, file_ids, max_age=CACHE_SECONDS):
    """
    Fetches the playstates merged() will need to write the Kodi files file_ids
    of the Plex item plex_id. Call it before taking any DB lock
    """
    if plex_type != v.PLEX_TYPE_EPISODE:
        return
    plex_id = int(plex_id)
    with PlexDB(lock=False) as plexdb:
        siblings = {sibling
                    for file_id in file_ids if file_id
                    for sibling in plexdb.episode_ids_by_file(file_id)
                    if sibling != plex_id}
    fetch_states(siblings, max_age)


def prefetch_all_shared():
    """
    Fetches the current playstate of all episodes sharing a Kodi file, for a
    full sync. Call it before taking any DB lock
    """
    with PlexDB(lock=False) as plexdb:
        plex_ids = plexdb.shared_episode_ids()
    fetch_states(plex_ids, 0)


def merged(plex_id, plex_type, file_id, playstate, plexdb=None):
    """
    Returns the playstate to write to the Kodi file file_id, given the
    playstate of the Plex item plex_id. For a file shared by several episodes
    that is the merge of all of their playstates. Returns None if the playstate
    of one of them is unknown (not prefetched, or the PMS did not answer): the
    file must then be left alone rather than getting a state that misses what
    was done with that episode. plexdb is optional, e.g. in a sync's context
    """
    if plex_type != v.PLEX_TYPE_EPISODE:
        return playstate
    plex_id = int(plex_id)
    if plexdb is None:
        with PlexDB(lock=False) as plexdb:
            siblings = plexdb.episode_ids_by_file(file_id)
    else:
        siblings = plexdb.episode_ids_by_file(file_id)
    playstates = {plex_id: playstate}
    for sibling in siblings:
        if sibling == plex_id:
            continue
        with _LOCK:
            cached = _STATES.get(sibling)
        if cached is None:
            LOG.warning('Playstate of episode %s unknown, leaving the Kodi '
                        'file %s of episode %s alone',
                        sibling, file_id, plex_id)
            return
        playstates[sibling] = cached[1]
    return merge_playstates(playstates)


def write(kodidb, plex_id, plex_type, file_ids, playstate):
    """
    Writes the playstate of the Plex item plex_id to the Kodi files file_ids,
    merged with the other episodes sharing a file. See prefetch()
    """
    for file_id in file_ids:
        if not file_id:
            continue
        merged_playstate = merged(plex_id, plex_type, file_id, playstate)
        if merged_playstate is not None:
            kodidb.set_resume(file_id, *merged_playstate)
