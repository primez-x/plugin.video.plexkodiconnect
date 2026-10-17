#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Project complete Plex memberships onto Kodi cards without provider requests."""
from copy import deepcopy
from contextlib import contextmanager
from logging import getLogger
import os
import re
import sqlite3
from uuid import uuid4

LOG = getLogger('PLEX.membership')
_MEDIA_TYPES = ('movie', 'show', 'season', 'episode')
_KODI_TYPES = ('movie', 'tvshow', 'season', 'episode')
_PREFIXES = {'watchlist': 'PKC.Watchlist', 'favourites': 'PKC.Favourites'}
_GENERATION = 'PKC.Membership.Generation'
_KEY = re.compile(r'[0-9a-f]{24}')
_GUID = re.compile(r'plex://(movie|show|season|episode)/([0-9a-f]{24})')
_DBID = re.compile(r'[1-9][0-9]{0,9}')


def canonical_guid(value, media_type=None):
    """Return a full canonical Plex GUID, or None for an invalid identity."""
    match = _GUID.fullmatch(value) if isinstance(value, str) else None
    if match and (media_type is None or match[1] == media_type):
        return value
    return None


def _account(account_hash):
    from . import discover_cache, utils
    active = discover_cache.active_account_hash()
    account_hash = active if account_hash is None else account_hash
    if (account_hash and account_hash == active
            and utils.window('PKC.Account.Hash') == account_hash):
        return account_hash
    return None


def _enqueue_refresh():
    from . import discover_cache
    watchlist = discover_cache.enqueue_refresh(discover_cache.WATCHLIST_URL, 'watchlist')
    favourites = discover_cache.enqueue_refresh(
        'https://community.plex.tv/api/favourites', 'community_membership')
    return watchlist and favourites


def _silence(prefix, account_hash):
    from . import utils
    for suffix in ('.Account', '.Library.Account'):
        if utils.window(prefix + suffix) == account_hash:
            utils.window(prefix + suffix, clear=True)


@contextmanager
def _publication_lock():
    """Hold cross-invoker ownership without a timeout or lease takeover."""
    from . import variables
    profile = variables.ADDON_PROFILE
    if not profile:
        yield False
        return
    connection = None
    try:
        os.makedirs(profile, exist_ok=True)
        connection = sqlite3.connect(
            os.path.join(profile, 'membership-publication.sqlite'), timeout=0)
        connection.execute('BEGIN EXCLUSIVE')
    except (OSError, sqlite3.Error):
        if connection is not None:
            connection.close()
        yield False
        return
    try:
        yield True
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def generation():
    """Return the source fence to capture before cache or network reads."""
    from . import utils
    return utils.window(_GENERATION)


def _clear(kind, account_hash, generation=None):
    from . import utils
    if (not _account(account_hash)
            or (generation is not None and utils.window(_GENERATION) != generation)):
        return False
    generation = uuid4().hex
    utils.window(_GENERATION, value=generation)
    prefix = _PREFIXES[kind]
    with _publication_lock() as locked:
        if locked:
            for suffix in ('.Account', '.Library.Account', '.Guids'):
                if (not _account(account_hash)
                        or utils.window(_GENERATION) != generation):
                    return False
                utils.window(prefix + suffix, clear=True)
        else:
            _silence(prefix, account_hash)
    if not locked:
        _enqueue_refresh()
    return False


def _store(kind, guids, account_hash, generation, buckets=None, preserve=False):
    from . import utils
    prefix = _PREFIXES[kind]
    value = '|' + '|'.join(sorted(guids)) + ('|' if guids else '')

    def current():
        return (_account(account_hash)
                and utils.window(_GENERATION) == generation)

    with _publication_lock() as locked:
        if locked:
            if not current():
                return False
            keep_native = (preserve and utils.window(prefix + '.Guids') == value
                           and utils.window(prefix + '.Account') == account_hash
                           and utils.window(prefix + '.Library.Account') == account_hash)
            try:
                utils.window(prefix + '.Account', clear=True)
                if not current():
                    return False
                utils.window(prefix + '.Guids', value=value)
                if not current():
                    return False
                if not keep_native:
                    utils.window(prefix + '.Library.Account', clear=True)
                    if not current():
                        return False
                    if buckets is not None:
                        for (kodi_type, digits), ids in buckets.items():
                            utils.window('%s.Library.%s.%d' % (prefix, kodi_type, digits),
                                         value='|' + '|'.join(sorted(ids, key=int))
                                         + ('|' if ids else ''))
                            if not current():
                                return False
                        utils.window(prefix + '.Library.Account', value=account_hash)
                        if not current():
                            return False
                # Confirm the complete publication after every native bucket succeeds.
                utils.window(prefix + '.Account', value=account_hash)
                return bool(current())
            finally:
                if not current():
                    _silence(prefix, account_hash)
        else:
            _silence(prefix, account_hash)
    _enqueue_refresh()
    return False


def _key_guids(kind, keys):
    guids = set()
    for key in keys:
        guid = canonical_guid(key)
        if guid:
            guids.add(guid)
        elif isinstance(key, str) and _KEY.fullmatch(key):
            media_types = _MEDIA_TYPES if kind == 'favourites' else ('movie', 'show')
            guids.update('plex://%s/%s' % (media_type, key) for media_type in media_types)
        else:
            raise ValueError('Invalid canonical Plex identity')
    return guids


def _project_ids(metadata, verified):
    from .plex_api import mass_api
    buckets = {(kodi_type, digits): set() for kodi_type in _KODI_TYPES
               for digits in range(1, 11)}
    matches = mass_api(deepcopy(metadata), check_by_guid=True,
                       match_external_ids=True) if metadata else ()
    for api in matches:
        guid = canonical_guid(api.xml.get('guid'), api.plex_type)
        if guid not in verified or api.kodi_type not in _KODI_TYPES:
            continue
        kodi_id = api.kodi_id
        if type(kodi_id) is int:
            kodi_id = str(kodi_id)
        if (not isinstance(kodi_id, str) or not _DBID.fullmatch(kodi_id)
                or int(kodi_id) > 2147483647):
            continue
        buckets[(api.kodi_type, len(kodi_id))].add(kodi_id)
    return buckets


def publish(kind, metadata, account_hash=None, complete=True, keys=None, generation=None):
    """Publish complete GUID membership and exact native matches from hydrated XML."""
    from . import utils
    _PREFIXES[kind]
    account_hash = _account(account_hash)
    if not account_hash:
        return False
    generation = utils.window(_GENERATION) if generation is None else generation
    if utils.window(_GENERATION) != generation:
        return False
    if not complete or (metadata is None and keys is None):
        return _clear(kind, account_hash, generation)
    try:
        guids = _key_guids(kind, keys) if keys is not None else set()
        source = []
        for row in metadata if metadata is not None else ():
            media_type = row.get('type')
            guid = canonical_guid(row.get('guid'), media_type)
            key = row.get('ratingKey')
            if (media_type not in _MEDIA_TYPES or not guid
                    or (key is not None and key != guid.rsplit('/', 1)[1])):
                if keys is None:
                    return _clear(kind, account_hash, generation)
                return _store(kind, guids, account_hash, generation)
            if keys is not None and guid not in guids:
                return _store(kind, guids, account_hash, generation)
            if keys is None:
                guids.add(guid)
            source.append(row)
        buckets = None
        if utils.window(_GENERATION) != generation or not _account(account_hash):
            return False
        if metadata is not None:
            try:
                buckets = _project_ids(source, guids)
            except Exception as error:
                LOG.warning('Could not match %s library membership: %s', kind, error)
        return _store(kind, guids, account_hash, generation, buckets=buckets)
    except Exception as error:
        LOG.warning('Could not project %s membership: %s', kind, error)
        return _clear(kind, account_hash, generation)


def publish_keys(kind, keys, account_hash=None, generation=None):
    """Publish complete GUID membership and preserve only a valid native projection."""
    from . import utils
    _PREFIXES[kind]
    account_hash = _account(account_hash)
    if not account_hash:
        return False
    generation = utils.window(_GENERATION) if generation is None else generation
    if utils.window(_GENERATION) != generation:
        return False
    if keys is None:
        return _clear(kind, account_hash, generation)
    try:
        return _store(kind, _key_guids(kind, keys), account_hash, generation, preserve=True)
    except Exception as error:
        LOG.warning('Could not project %s membership: %s', kind, error)
        return _clear(kind, account_hash, generation)


def update_watchlist(rating_key, present, account_hash=None):
    """Apply a verified add/remove to the current account's complete projection."""
    from . import utils
    account_hash = _account(account_hash)
    if (not account_hash or not isinstance(rating_key, str)
            or not _KEY.fullmatch(rating_key)):
        return False
    generation = uuid4().hex
    utils.window(_GENERATION, value=generation)
    if utils.window('PKC.Watchlist.Account') != account_hash:
        return False
    value = utils.window('PKC.Watchlist.Guids') or ''
    if not value:
        return False
    guids = {guid for guid in value.split('|') if canonical_guid(guid)}
    if present:
        guids.update('plex://%s/%s' % (media_type, rating_key)
                     for media_type in ('movie', 'show'))
    else:
        guids = {guid for guid in guids if guid.rsplit('/', 1)[1] != rating_key}
    return _store('watchlist', guids, account_hash, generation)


def invalidate_library():
    """Invalidate native IDs and queue the existing service refresh jobs."""
    from . import utils
    utils.window(_GENERATION, value=uuid4().hex)
    for prefix in _PREFIXES.values():
        utils.window(prefix + '.Library.Account', clear=True)
    return _enqueue_refresh()


def reset_account(token):
    """Publish the cache namespace and clear memberships after account changes."""
    from . import discover_cache, utils
    account_hash = discover_cache.account_hash_for_token(token)
    if account_hash and account_hash == utils.window('PKC.Account.Hash'):
        return account_hash
    utils.window(_GENERATION, value=uuid4().hex)
    utils.window('PKC.Account.Hash', clear=True)
    for prefix in _PREFIXES.values():
        utils.window(prefix + '.Account', clear=True)
        utils.window(prefix + '.Library.Account', clear=True)
        utils.window(prefix + '.Guids', clear=True)
    utils.window('PKC.Favourites.ListName', clear=True)
    if account_hash:
        utils.window('PKC.Account.Hash', value=account_hash)
    return account_hash
