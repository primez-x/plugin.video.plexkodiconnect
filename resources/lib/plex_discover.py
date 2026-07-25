#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Kodi-independent validation for Plex Discover metadata identities."""


METADATA_MATCH_URL = "https://metadata.provider.plex.tv/library/metadata/matches"


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
