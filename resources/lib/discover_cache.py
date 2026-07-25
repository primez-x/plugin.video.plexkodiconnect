#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Small, account-scoped session cache for Plex Discover provider XML.

Kodi starts a separate plugin process for each widget.  A Home-window cache is
therefore the only cheap process-independent cache available on the hot listing
path.  Entries are compressed, bounded, and keyed by a one-way account
fingerprint; a successful Watchlist mutation advances the generation so stale
Discover rankings cannot be reused.
"""

from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from hashlib import sha256
from time import time
from uuid import uuid4
import xml.etree.ElementTree as etree
import zlib

from . import utils


CACHE_VERSION = "1"
CACHE_PREFIX = "PKC.Discover.Cache."
CACHE_GENERATION = CACHE_PREFIX + "Generation"
CACHE_INDEX = CACHE_PREFIX + "Index"
CACHE_PAYLOAD_SUFFIX = ".Payload"
CACHE_UPDATED_SUFFIX = ".Updated"
CACHE_WRAPPER_TAG = "PKCDiscoverCache"
CACHE_ENTRY_LIMIT = 24
CACHE_MAX_RAW_BYTES = 512 * 1024
CACHE_MAX_ENCODED_BYTES = 96 * 1024


def _generation():
    generation = utils.window(CACHE_GENERATION)
    if generation:
        return generation
    generation = uuid4().hex
    utils.window(CACHE_GENERATION, value=generation)
    return generation


def _cache_key(url):
    """Return a non-secret cache key isolated to the active Plex account."""
    token = utils.window("plex_token")
    if not token or not isinstance(url, str) or not url:
        return None
    material = "\x00".join((CACHE_VERSION, str(token), _generation(), url))
    return sha256(material.encode("utf-8")).hexdigest()


def _payload_property(key):
    return CACHE_PREFIX + key + CACHE_PAYLOAD_SUFFIX


def _updated_property(key):
    return CACHE_PREFIX + key + CACHE_UPDATED_SUFFIX


def _index():
    return [key for key in utils.window(CACHE_INDEX).split("|") if key]


def _clear(key):
    utils.window(_payload_property(key), clear=True)
    utils.window(_updated_property(key), clear=True)


def _remember_key(key):
    keys = [cached_key for cached_key in _index() if cached_key != key]
    keys.append(key)
    while len(keys) > CACHE_ENTRY_LIMIT:
        _clear(keys.pop(0))
    utils.window(CACHE_INDEX, value="|".join(keys))


def get_xml(url, max_age_seconds):
    """Return a fresh cached provider response, or ``None`` on a cache miss."""
    key = _cache_key(url)
    if key is None:
        return None
    try:
        max_age_seconds = float(max_age_seconds)
    except (TypeError, ValueError):
        return None
    try:
        updated = float(utils.window(_updated_property(key)))
    except (TypeError, ValueError):
        return None
    if max_age_seconds <= 0 or time() - updated > max_age_seconds:
        return None
    encoded = utils.window(_payload_property(key))
    if not encoded or len(encoded) > CACHE_MAX_ENCODED_BYTES:
        return None
    try:
        root = etree.fromstring(zlib.decompress(b64decode(encoded.encode("ascii"))))
    except (BinasciiError, TypeError, ValueError, zlib.error, etree.ParseError):
        _clear(key)
        return None
    if root.tag != CACHE_WRAPPER_TAG:
        _clear(key)
        return None
    return list(root)


def put_xml(url, xmls):
    """Store parsed provider roots when their serialized form is bounded."""
    key = _cache_key(url)
    if key is None or not xmls:
        return False
    try:
        wrapper = etree.Element(CACHE_WRAPPER_TAG)
        for xml in xmls:
            wrapper.append(xml)
        raw = etree.tostring(wrapper, encoding="utf-8")
    except (AttributeError, TypeError, ValueError):
        return False
    if not raw or len(raw) > CACHE_MAX_RAW_BYTES:
        return False
    encoded = b64encode(zlib.compress(raw)).decode("ascii")
    if len(encoded) > CACHE_MAX_ENCODED_BYTES:
        return False
    utils.window(_payload_property(key), value=encoded)
    utils.window(_updated_property(key), value=str(time()))
    _remember_key(key)
    return True


def invalidate():
    """Make every existing Discover response ineligible after a state change."""
    # A generation rather than only index deletion prevents an in-flight fetch
    # from republishing an old ranking after a Watchlist mutation completes.
    utils.window(CACHE_GENERATION, value=uuid4().hex)
    for key in _index():
        _clear(key)
    utils.window(CACHE_INDEX, clear=True)
