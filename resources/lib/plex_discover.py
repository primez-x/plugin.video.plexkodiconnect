#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Kodi-independent validation for Plex Discover metadata identities."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

METADATA_MATCH_URL = "https://metadata.provider.plex.tv/library/metadata/matches"
METADATA_ITEM_URL = "https://metadata.provider.plex.tv/library/metadata/%s"
DISCOVER_PROVIDER_HOST = "discover.provider.plex.tv"
METADATA_PROVIDER_HOST = "metadata.provider.plex.tv"
DISCOVER_PREFERRED_SERVICES_KEY = "x-plex-preferred-services[]"
DISCOVER_PREFERRED_SERVICES_BATCH_SIZE = 20


def _as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def normalize_tmdb_type(value):
    """Normalize Kodi/TMDb labels to the type expected by Plex's match API."""
    value = str(value or "").strip().lower()
    if value == "movie":
        return "movie"
    if value in ("tv", "show", "tvshow"):
        return "tv"
    return None


def tmdb_watchlist_identity(tmdb_id, tmdb_type):
    """Return the stable Home-window identity for one TMDb Watchlist item."""
    parameters = tmdb_match_parameters(tmdb_id, tmdb_type)
    media_type = normalize_tmdb_type(tmdb_type)
    if parameters is None or media_type is None:
        return None
    return "tmdb.%s.%s" % (media_type, str(tmdb_id).strip())


def normalize_rating_key(value):
    """Accept only opaque Plex rating keys before placing one in a URL path."""
    value = str(value or "").strip()
    if not value or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for character in value
    ):
        return None
    return value


def provider_redirect_urls(
    location,
    allowed_hosts,
    batch_size=DISCOVER_PREFERRED_SERVICES_BATCH_SIZE,
):
    """Return trusted provider redirect URLs with bounded preferred-service lists.

    Plex can redirect a Discover request with an account-specific preferred
    services query that is large enough for the next request to be rejected
    with HTTP 431.  The redirect remains authoritative, but it must be split
    into request-safe batches before following it.
    """
    if not isinstance(location, str) or not isinstance(
        allowed_hosts, (tuple, list, set)
    ):
        return []
    if not isinstance(batch_size, int) or batch_size < 1:
        return []
    try:
        parsed = urlsplit(location)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return []
    trusted_hosts = {str(host).lower() for host in allowed_hosts}
    if (
        parsed.scheme.lower() != "https"
        or hostname not in trusted_hosts
        or parsed.username
        or parsed.password
        or port is not None
    ):
        return []
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    preferred_services = [
        value for key, value in query_items if key == DISCOVER_PREFERRED_SERVICES_KEY
    ]
    if len(preferred_services) <= batch_size:
        return [location]
    base_query = [
        (key, value)
        for key, value in query_items
        if key != DISCOVER_PREFERRED_SERVICES_KEY
    ]
    urls = []
    for start in range(0, len(preferred_services), batch_size):
        query = base_query + [
            (DISCOVER_PREFERRED_SERVICES_KEY, value)
            for value in preferred_services[start : start + batch_size]
        ]
        urls.append(
            urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    urlencode(query),
                    "",
                )
            )
        )
    return urls


def watchlist_state_from_payload(payload, rating_key):
    """Return whether a canonical Plex item is watchlisted, or ``None`` if invalid."""
    rating_key = normalize_rating_key(rating_key)
    if rating_key is None or not isinstance(payload, dict):
        return None
    container = payload.get("MediaContainer")
    if not isinstance(container, dict):
        return None
    user_states = _as_list(container.get("UserState"))
    if not user_states:
        # Plex represents an unwatchlisted item as an empty UserState collection.
        return False
    for user_state in user_states:
        if not isinstance(user_state, dict):
            continue
        response_rating_key = user_state.get("ratingKey")
        if response_rating_key is not None and str(response_rating_key) != rating_key:
            continue
        return bool(user_state.get("watchlistedAt"))
    return None


def tmdb_match_parameters(tmdb_id, tmdb_type):
    """Return exact Plex metadata-match parameters, or ``None`` if invalid."""
    tmdb_id = str(tmdb_id or "").strip()
    media_type = normalize_tmdb_type(tmdb_type)
    if not tmdb_id.isdigit() or media_type is None:
        return None
    return {
        "guid": "tmdb://%s" % tmdb_id,
        "type": 1 if media_type == "movie" else 2,
    }


def _plex_media_type(value):
    value = str(value or "").strip().lower()
    if value in ("movie", "1"):
        return "movie"
    if value in ("show", "tv", "tvshow", "2"):
        return "show"
    return None


def _rating_key_from_guid(guid, expected_type):
    if not isinstance(guid, str) or not guid.startswith("plex://"):
        return None
    guid_type, separator, rating_key = guid[len("plex://") :].partition("/")
    if (
        not separator
        or guid_type != expected_type
        or not rating_key
        or "/" in rating_key
        or "?" in rating_key
        or "#" in rating_key
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in rating_key
        )
    ):
        return None
    return rating_key


def resolve_tmdb_match(payload, tmdb_type):
    """
    Return one exact canonical Plex match, or ``None`` if it is not unique.

    A display title and release year are deliberately absent from this path.
    Watchlist mutations receive only the opaque rating key from Plex's own
    canonical ``plex://`` GUID.
    """
    requested_type = normalize_tmdb_type(tmdb_type)
    expected_type = {"movie": "movie", "tv": "show"}.get(requested_type)
    if expected_type is None or not isinstance(payload, dict):
        return None
    container = payload.get("MediaContainer")
    if not isinstance(container, dict):
        return None

    matches = {}
    for metadata in _as_list(container.get("Metadata")):
        if not isinstance(metadata, dict):
            continue
        if _plex_media_type(metadata.get("type")) != expected_type:
            continue
        guid = metadata.get("guid")
        rating_key = _rating_key_from_guid(guid, expected_type)
        if rating_key is not None:
            metadata_rating_key = metadata.get("ratingKey")
            if (
                metadata_rating_key is not None
                and str(metadata_rating_key) != rating_key
            ):
                continue
            matches[guid] = {"guid": guid, "rating_key": rating_key}
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))
