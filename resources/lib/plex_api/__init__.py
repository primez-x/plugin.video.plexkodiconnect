#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
plex_api interfaces with all Plex Media Server (and plex.tv) xml responses
"""
import copy
from logging import getLogger

from .base import Base
from .artwork import Artwork
from .file import File
from .media import Media
from .user import User
from .playback import Playback

from ..plex_db import PlexDB

LOG = getLogger('PLEX.api')

# Metadata provider ids that identify a synced library item without an exact
# guid match, in order of preference
EXTERNAL_ID_PROVIDERS = ('tmdb', 'imdb', 'tvdb')


class API(Base, Artwork, File, Media, User, Playback):
    pass


def _external_ids(api):
    """
    Returns the {provider: id} of api's EXTERNAL_ID_PROVIDERS, e.g. parsed
    from the <Guid id="tmdb://603"/> children of a plex.tv provider item
    """
    try:
        guids = api.guids
    except (AttributeError, IndexError, TypeError):
        # Malformed <Guid> child
        return {}
    ids = {}
    for provider in EXTERNAL_ID_PROVIDERS:
        value = str(guids.get(provider) or '').strip()
        if value:
            ids[provider] = value
    return ids


def _items_by_external_ids(apis, plexdb):
    """
    Pass in a list of (index, api) without an exact guid match. Returns
    {index: [db_item, ...]} for every api whose TMDb, IMDb or TVDB id (in
    that order) belongs to a synced Plex library item of the same type.

    A PMS item matched by a legacy agent carries e.g. the guid
    com.plexapp.agents.imdb://tt0133093 instead of the plex:// guid of the
    plex.tv provider. PKC syncs every item's provider ids to Kodi's uniqueid
    table, so a single local Kodi DB read covers all apis.
    """
    from .. import variables as v
    from ..kodi_db import KodiVideoDB
    kodi_types = {v.PLEX_TYPE_MOVIE: v.KODI_TYPE_MOVIE,
                  v.PLEX_TYPE_SHOW: v.KODI_TYPE_SHOW}
    wanted = []
    values = {}
    for index, api in apis:
        kodi_type = kodi_types.get(api.plex_type)
        ids = _external_ids(api) if kodi_type else None
        if ids:
            wanted.append((index, kodi_type, ids))
            values.setdefault(kodi_type, set()).update(ids.values())
    if not wanted:
        return {}
    try:
        with KodiVideoDB(lock=False) as kodidb:
            kodi_ids = {kodi_type: kodidb.kodi_ids_by_uniqueids(kodi_type,
                                                                type_values)
                        for kodi_type, type_values in values.items()}
    except Exception as err:
        # Never fail a listing; the items stay provider-only
        LOG.warning('Could not match provider ids in the Kodi DB: %s', err)
        return {}
    matches = {}
    for index, kodi_type, ids in wanted:
        for provider in EXTERNAL_ID_PROVIDERS:
            if provider not in ids:
                continue
            items = []
            for kodi_id in sorted(set(
                    kodi_ids[kodi_type].get((provider, ids[provider]), ()))):
                # Not every Kodi library item was synced by PKC
                item = plexdb.item_by_kodi_id(kodi_id, kodi_type)
                if item:
                    items.append(item)
            if items:
                matches[index] = items
                break
    return matches


def mass_api(xml, check_by_guid=False, match_external_ids=False):
    """
    Pass in an entire XML PMS response with e.g. several movies or episodes
    Will Look-up Kodi ids in the Plex.db for every element (thus speeding up
    this process for several PMS items!)
    Avoid a lookup by guid instead of the plex id (check_by_guid=True) as
    it will be way slower
    With match_external_ids=True, an item without an exact guid match is
    matched by its TMDb, IMDb or TVDB id instead (movies and shows only)
    """
    apis = [API(x) for x in xml]
    if check_by_guid:
        # A single guid might return a bunch of different plex id's
        # We extend the xml list with these ids
        new_apis = list()
        with PlexDB(lock=False) as plexdb:
            matches = [api.check_by_guid(plexdb=plexdb) for api in apis]
            if match_external_ids:
                unmatched = [(index, api) for index, api in enumerate(apis)
                             if not matches[index]]
                if unmatched:
                    for index, items in _items_by_external_ids(
                            unmatched, plexdb).items():
                        matches[index] = items
            for api, items in zip(apis, matches):
                # Keep a pristine copy so every additional match (e.g. movie
                # editions) gets its own XML element and API instance instead
                # of all entries pointing at the last match.
                original_xml = copy.deepcopy(api.xml) if len(items) > 1 \
                    else None
                for index, item in enumerate(items):
                    new_api = api if index == 0 else \
                        API(copy.deepcopy(original_xml))
                    # Since we cannot simply set the plex_id and type.
                    # This will overwrite a weird "guid ratingKey" that
                    # plex set, originally looking e.g. like
                    # ratingKey="5d776883ebdf2200209c104e"
                    new_api.xml.set('ratingKey', str(item['plex_id']))
                    new_api.xml.set('key', f'/library/metadata/{item["plex_id"]}')
                    new_api.check_db(plexdb=plexdb)
                    new_apis.append(new_api)
        return new_apis
    else:
        with PlexDB(lock=False) as plexdb:
            for api in apis:
                api.check_db(plexdb=plexdb)
        return apis
