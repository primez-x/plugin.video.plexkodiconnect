#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Helpers for keeping PMS-hosted artwork URLs aligned with the active connection.
"""
import os
from urllib.parse import urlsplit, urlunsplit


PLEX_PHOTO_TRANSCODE_PATH = '/photo/:/transcode'
# Records which (Kodi video DB, PMS base URL) the Kodi art table was last
# normalized for, so unchanged startups skip the full-table scan
NORMALIZED_MARKER_FILENAME = 'artwork_urls_normalized.txt'


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


def normalization_marker(db_path, server):
    """
    Identity of a completed table normalization: the Kodi video DB file plus
    the scheme://host:port of the PMS that every rewritten URL now points to.
    Returns None if server is not a usable base URL.
    """
    if not server:
        return None
    parts = urlsplit(server)
    if not parts.scheme or not parts.netloc:
        return None
    return '%s\n%s://%s' % (db_path or '', parts.scheme, parts.netloc.lower())


def read_normalization_marker(directory):
    try:
        with open(os.path.join(directory, NORMALIZED_MARKER_FILENAME),
                  'r', encoding='utf-8') as marker:
            return marker.read()
    except (OSError, IOError, ValueError, TypeError):
        return None


def write_normalization_marker(directory, marker):
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, NORMALIZED_MARKER_FILENAME)
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as marker_file:
            marker_file.write(marker)
        os.replace(tmp_path, path)
        return True
    except (OSError, IOError, ValueError, TypeError):
        return False


def clear_normalization_marker(directory):
    try:
        os.remove(os.path.join(directory, NORMALIZED_MARKER_FILENAME))
    except FileNotFoundError:
        pass
    except (OSError, IOError, ValueError, TypeError):
        return False
    return True
