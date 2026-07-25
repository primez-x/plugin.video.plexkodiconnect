#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Small, Kodi-independent helpers for Plex Discover provider responses.

Plex Discover uses opaque ``plex://`` GUIDs for watchlist actions.  Keep the
identity validation here so every caller follows the same strict path and no
caller is tempted to fall back to a title/year match.
"""


DISCOVER_HOME_URL = 'https://discover.provider.plex.tv/hubs/sections/home'
DISCOVER_SEARCH_URL = 'https://discover.provider.plex.tv/library/search'
METADATA_MATCH_URL = 'https://metadata.provider.plex.tv/library/metadata/matches'
WATCHLIST_ACTION_URL = 'https://discover.provider.plex.tv/actions/addToWatchlist'


def _as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def normalize_tmdb_type(value):
    """
    Normalizes Kodi/TMDb media-type labels to the matching Plex API type.

    ``tvshow`` is what Kodi exposes in ``ListItem.DBType`` while Plex's
    metadata match API uses the numeric type for a ``show``.
    """
    value = str(value or '').strip().lower()
    if value == 'movie':
        return 'movie'
    if value in ('tv', 'show', 'tvshow'):
        return 'tv'
    return None


def plex_media_type(value):
    """Return the canonical Plex media type (movie/show), if supported."""
    value = str(value or '').strip().lower()
    if value in ('movie', '1'):
        return 'movie'
    if value in ('show', 'tv', 'tvshow', '2'):
        return 'show'
    return None


def tmdb_match_parameters(tmdb_id, tmdb_type):
    """
    Build validated exact-match parameters or return ``None``.

    TMDb IDs are numeric.  Rejecting everything else is intentional: this is
    a mutation route and must never reinterpret a display title as identity.
    """
    tmdb_id = str(tmdb_id or '').strip()
    media_type = normalize_tmdb_type(tmdb_type)
    if not tmdb_id.isdigit() or media_type is None:
        return None
    return {
        'guid': 'tmdb://%s' % tmdb_id,
        'type': 1 if media_type == 'movie' else 2
    }


def canonical_plex_guid(metadata, expected_type=None):
    """
    Return a valid Plex Discover GUID for a movie/show metadata record.

    The provider's ``ratingKey`` is derived from this GUID rather than trusted
    independently, keeping a single canonical identity source.
    """
    if not isinstance(metadata, dict):
        return None
    guid = metadata.get('guid')
    if not isinstance(guid, str) or not guid.startswith('plex://'):
        return None
    guid_type, separator, opaque_key = guid[len('plex://'):].partition('/')
    if not separator:
        return None
    if guid_type not in ('movie', 'show'):
        return None
    if not opaque_key or '/' in opaque_key or '?' in opaque_key or '#' in opaque_key:
        return None
    metadata_type = plex_media_type(metadata.get('type'))
    if metadata_type is not None and metadata_type != guid_type:
        return None
    expected_type = plex_media_type(expected_type)
    if expected_type is not None and guid_type != expected_type:
        return None
    return guid


def rating_key_from_guid(guid):
    """Extract the opaque provider action key from a validated Plex GUID."""
    canonical_guid = canonical_plex_guid({'guid': guid})
    if canonical_guid is None:
        return None
    return canonical_guid.rsplit('/', 1)[-1]


def _video_metadata(records):
    """Filter a metadata sequence to supported, canonical Discover records."""
    metadata = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        media_type = plex_media_type(record.get('type'))
        guid = canonical_plex_guid(record, media_type)
        if media_type is None or guid is None or guid in seen:
            continue
        seen.add(guid)
        metadata.append(record)
    return metadata


def discover_search_metadata(payload):
    """
    Extract only Plex's external movie/show search results.

    Provider search can also return local server and people/search groups.
    Those are deliberately ignored: this route is a Discover surface and its
    cards must carry provider-owned canonical identities.
    """
    if not isinstance(payload, dict):
        return []
    container = payload.get('MediaContainer')
    if not isinstance(container, dict):
        return []

    metadata = []
    for group in _as_list(container.get('SearchResults')):
        if not isinstance(group, dict):
            continue
        if group.get('id') != 'external':
            continue
        for result in _as_list(group.get('SearchResult')):
            if isinstance(result, dict):
                metadata.extend(_as_list(result.get('Metadata')))
    return _video_metadata(metadata)


def discover_home_hubs(payload):
    """Extract nonempty Discover home hubs with canonical video metadata."""
    if not isinstance(payload, dict):
        return []
    container = payload.get('MediaContainer')
    if not isinstance(container, dict):
        return []

    hubs = []
    for hub in _as_list(container.get('Hub')):
        if not isinstance(hub, dict):
            continue
        metadata = _video_metadata(_as_list(hub.get('Metadata')))
        if metadata:
            hubs.append({
                'title': hub.get('title') or 'Plex Discover',
                'metadata': metadata
            })
    return hubs


def resolve_tmdb_match(payload, tmdb_type):
    """
    Return the sole exact Plex match for a validated TMDb type, if any.

    A missing, malformed, wrong-type, or ambiguous response is intentionally
    not resolved.  The caller can notify the user without touching Watchlist.
    """
    requested_type = normalize_tmdb_type(tmdb_type)
    expected_type = 'movie' if requested_type == 'movie' else 'show' \
        if requested_type == 'tv' else None
    if expected_type is None or not isinstance(payload, dict):
        return None
    container = payload.get('MediaContainer')
    if not isinstance(container, dict):
        return None

    matches = {}
    for metadata in _as_list(container.get('Metadata')):
        if not isinstance(metadata, dict):
            continue
        if plex_media_type(metadata.get('type')) != expected_type:
            continue
        guid = canonical_plex_guid(metadata, expected_type)
        if guid is None:
            continue
        rating_key = rating_key_from_guid(guid)
        if rating_key is not None:
            matches[guid] = {
                'guid': guid,
                'rating_key': rating_key,
                'type': expected_type
            }
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))
