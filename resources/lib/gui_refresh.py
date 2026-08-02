#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger
import re
from uuid import uuid4

import xbmc

from .plex_db import PlexDB
from . import variables as v


LOG = getLogger('PLEX.gui_refresh')
CONTAINERS_LABEL = 'Window(10000).Property(PKC.SidePanelContainers)'
TVSHOW_PATHS = (
    re.compile(r'^videodb://tvshows/titles/(\d+)(?:/|\?|$)'),
    re.compile(r'^videodb://tvshows/genres/\d+/(\d+)(?:/|\?|$)'),
)


def refresh_sidepanel_containers(changed_items):
    """
    Invalidate opted-in nested TV containers affected by changed episodes.

    Skins declare container IDs through PKC.SidePanelContainers and include
    PKC.SyncToken.<id> in each dynamic content URL. Changing that token makes
    Kodi rebuild the nested listing and discard cached CVideoInfoTag objects.
    """
    try:
        episode_ids = tuple(
            plex_id for plex_id, plex_type in changed_items
            if plex_type == v.PLEX_TYPE_EPISODE)
        if not episode_ids:
            return
        container_ids = tuple(_declared_container_ids())
        if not container_ids:
            return
        affected_show_ids = _affected_show_ids(episode_ids)
        token = uuid4().hex
        for container_id in container_ids:
            _refresh_container(container_id, affected_show_ids, token)
    except Exception:
        LOG.exception('Could not refresh side-panel containers')


def _declared_container_ids():
    seen = set()
    value = xbmc.getInfoLabel(CONTAINERS_LABEL)
    for container_id in value.split('|'):
        try:
            container_id = int(container_id.strip())
        except ValueError:
            continue
        if container_id in seen:
            continue
        seen.add(container_id)
        yield container_id


def _tvshow_id(folder_path):
    for pattern in TVSHOW_PATHS:
        match = pattern.match(folder_path)
        if match:
            return int(match.group(1))


def _affected_show_ids(episode_ids):
    show_ids = set()
    try:
        with PlexDB(lock=False) as plexdb:
            for plex_id in episode_ids:
                episode = plexdb.episode(plex_id)
                if not episode:
                    continue
                try:
                    show_ids.add(int(episode.get('grandparent_id')))
                except (TypeError, ValueError):
                    continue
    except Exception:
        LOG.exception('Could not resolve changed episodes to TV shows')
        return None
    return show_ids


def _refresh_container(container_id, affected_show_ids, token):
    try:
        folder_path = xbmc.getInfoLabel(
            'Container(%d).FolderPath' % container_id)
        if not folder_path or not folder_path.startswith(
                'videodb://tvshows/'):
            return
        show_id = _tvshow_id(folder_path)
        if affected_show_ids is not None \
                and show_id is not None \
                and show_id not in affected_show_ids:
            return
        xbmc.executebuiltin(
            'SetProperty(PKC.SyncToken.%d,%s,10000)'
            % (container_id, token))
        LOG.info('Signaled side-panel container %d reload: %s',
                 container_id, folder_path)
    except Exception:
        LOG.exception('Could not refresh side-panel container %d',
                      container_id)
