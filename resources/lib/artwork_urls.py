#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Helpers for keeping PMS-hosted artwork URLs aligned with the active connection.
"""
from urllib.parse import urlsplit, urlunsplit


PLEX_PHOTO_TRANSCODE_PATH = '/photo/:/transcode'


def _configured_server():
    try:
        from . import app
        return getattr(getattr(app, 'CONN', None), 'server', None)
    except Exception:
        return None


def normalize_plex_artwork_url(url, server=None):
    """
    Rewrite PMS photo-transcode artwork URLs through the current PMS connection.

    Kodi can keep old artwork URLs in its database after PKC's selected Plex
    connection changes. Reusing the stale host is enough to make widgets render
    blank thumbnails, even when the artwork path itself is valid.
    """
    if not isinstance(url, str) or not url:
        return url
    server = server or _configured_server()
    if not server:
        return url
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    if not parsed.path.startswith(PLEX_PHOTO_TRANSCODE_PATH):
        return url
    server_parts = urlsplit(server)
    if not server_parts.scheme or not server_parts.netloc:
        return url
    if parsed.scheme == server_parts.scheme and parsed.netloc == server_parts.netloc:
        return url
    return urlunsplit((
        server_parts.scheme,
        server_parts.netloc,
        parsed.path,
        parsed.query,
        parsed.fragment,
    ))


def normalize_artwork_table_urls(cursor, server=None):
    """
    Normalize existing Kodi video DB artwork rows.

    Returns the number of changed rows. The caller owns transaction commit.
    """
    server = server or _configured_server()
    if not server:
        return 0
    rows = cursor.execute(
        "SELECT art_id, url FROM art WHERE url LIKE ?",
        ('%/photo/:/transcode%',),
    ).fetchall()
    updates = []
    for art_id, url in rows:
        normalized = normalize_plex_artwork_url(url, server=server)
        if normalized != url:
            updates.append((normalized, art_id))
    if updates:
        cursor.executemany(
            "UPDATE art SET url = ? WHERE art_id = ?",
            updates,
        )
    return len(updates)
