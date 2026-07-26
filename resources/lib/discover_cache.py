#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Crash-safe, account-scoped cache for Plex Discover provider metadata.

Kodi creates a new Python process for most plug-in listings. The cache keeps
small responses in Home-window properties for the current process and stores
the complete provider response on disk for subsequent processes. A cached hub
already contains its cards, while a cached metadata endpoint is the durable
item-detail cache. Keeping one atomic record per provider response avoids
per-card flash writes on the foreground navigation path.
"""

from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from logging import getLogger
import os
from time import sleep, time
from urllib.parse import urlsplit
from uuid import uuid4
import xml.etree.ElementTree as etree
import zlib

from . import plex_discover, utils, variables as v


LOG = getLogger("PLEX.discover_cache")

CACHE_VERSION = "v3"
RECORD_SCHEMA_VERSION = 1
CACHE_PREFIX = "PKC.Discover.Cache."
CACHE_INDEX = CACHE_PREFIX + "Index"
CACHE_PAYLOAD_SUFFIX = ".Payload"
CACHE_UPDATED_SUFFIX = ".Updated"
CACHE_OVERLAY_PREFIX = CACHE_PREFIX + "Watchlist."
CACHE_MAINTENANCE_REQUESTED = CACHE_PREFIX + "Maintenance.Requested"
CACHE_MAINTENANCE_COMPLETED = CACHE_PREFIX + "Maintenance.Completed"
CACHE_REFRESH_REQUESTED = CACHE_PREFIX + "Refresh.Requested"
CACHE_PREFETCH_REQUEST = CACHE_PREFIX + "Prefetch.Request"
CACHE_WRAPPER_TAG = "PKCDiscoverCache"

CACHE_ENTRY_LIMIT = 24
CACHE_MAX_SOURCE_RECORDS = 128
CACHE_MAX_RAW_BYTES = 512 * 1024
CACHE_MAX_ENCODED_BYTES = 768 * 1024
CACHE_L1_MAX_ENCODED_BYTES = 96 * 1024
CACHE_MAX_DISK_BYTES = 128 * 1024 * 1024
CACHE_RETENTION_SECONDS = 14 * 24 * 60 * 60
CACHE_TOUCH_INTERVAL_SECONDS = 60 * 60
CACHE_LOCK_STALE_SECONDS = 60
CACHE_TEMP_RETENTION_SECONDS = 60 * 60
CACHE_MAINTENANCE_INTERVAL_SECONDS = 30 * 60
WATCHLIST_OVERLAY_SECONDS = 5 * 60
REFRESH_REQUEST_RETENTION_SECONDS = 24 * 60 * 60
REFRESH_RETRY_LIMIT_SECONDS = 10 * 60
REFRESH_REQUEST_LIMIT = 32
REFRESH_LEASE_SECONDS = 60
REFRESH_FINISH_ATTEMPTS = 3
REFRESH_FINISH_RETRY_SECONDS = 0.05
REFRESH_PRIORITY_FOREGROUND = 0
REFRESH_PRIORITY_PREFETCH = 1
PREFETCH_HUB_LIMIT = 3

CACHE_FRESH = "fresh"
CACHE_STALE = "stale"
CACHE_MISS = "miss"

# A Discover source preserves ranked membership/order. Plex does not expose a
# Discover delta API, so a stale source renders locally and is revalidated by
# the low-priority service worker.
CACHE_POLICIES = {
    "catalog": {"fresh": 6 * 60 * 60},
    "hub": {"fresh": 2 * 60 * 60},
    "detail": {"fresh": 24 * 60 * 60},
}
REFRESH_HOSTS = {
    "catalog": (
        getattr(plex_discover, "DISCOVER_PROVIDER_HOST", "discover.provider.plex.tv"),
    ),
    "hub": (
        getattr(plex_discover, "DISCOVER_PROVIDER_HOST", "discover.provider.plex.tv"),
    ),
    "detail": (
        getattr(plex_discover, "METADATA_PROVIDER_HOST", "metadata.provider.plex.tv"),
    ),
}


def account_hash_for_token(token):
    """Return a non-secret stable cache namespace for a Plex access token."""
    if not token:
        return None
    return sha256(str(token).encode("utf-8")).hexdigest()


def active_account_hash():
    """Return the cache namespace for the currently active Plex token."""
    return account_hash_for_token(_window_get("plex_token"))


def account_matches(account_hash):
    """Whether account_hash still names the active Plex account."""
    return bool(account_hash) and account_hash == active_account_hash()


def _valid_account_hash(account_hash):
    return (
        isinstance(account_hash, str)
        and len(account_hash) == 64
        and all(character in "0123456789abcdef" for character in account_hash)
    )


def _profile_root():
    profile = getattr(v, "ADDON_PROFILE", None)
    if not profile:
        return None
    return os.path.join(profile, "discover-cache", CACHE_VERSION)


def _cache_root(account_hash=None):
    account_hash = active_account_hash() if account_hash is None else account_hash
    profile_root = _profile_root()
    if not profile_root or not _valid_account_hash(account_hash):
        return None
    return os.path.join(profile_root, account_hash)


def _directory(name, account_hash=None):
    root = _cache_root(account_hash)
    return os.path.join(root, name) if root else None


def _window_get(key):
    try:
        return utils.window(key) or ""
    except Exception:
        return ""


def _window_set(key, value):
    try:
        utils.window(key, value=value)
        return True
    except Exception:
        return False


def _window_clear(key):
    try:
        utils.window(key, clear=True)
        return True
    except Exception:
        return False


def _ensure_directory(directory):
    if not directory:
        return False
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as err:
        LOG.debug("Could not create Plex Discover cache directory %s: %s", directory, err)
        return False
    return True


def _listdir(directory):
    if not directory:
        return ()
    try:
        return os.listdir(directory)
    except OSError:
        return ()


def _safe_remove(filename):
    if not filename:
        return False
    try:
        os.remove(filename)
    except OSError:
        return False
    return True


def _safe_mtime(filename, fallback=0):
    try:
        return os.path.getmtime(filename)
    except OSError:
        return fallback


def _lock_owner(filename):
    try:
        with open(filename, "r", encoding="ascii") as lock_file:
            return lock_file.read()
    except (OSError, UnicodeError):
        return None


@contextmanager
def _lock(lock_root, name):
    """Acquire a short-lived cross-process lock without blocking Kodi UI work."""
    if not _ensure_directory(lock_root):
        yield False
        return
    filename = os.path.join(lock_root, "%s.lock" % name)
    descriptor = None
    owner = uuid4().hex
    try:
        descriptor = os.open(filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time() - _safe_mtime(filename, time()) > CACHE_LOCK_STALE_SECONDS:
            _safe_remove(filename)
            try:
                descriptor = os.open(filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError:
                descriptor = None
    except OSError:
        descriptor = None
    if descriptor is None:
        yield False
        return
    try:
        try:
            os.write(descriptor, owner.encode("ascii"))
        except OSError:
            try:
                os.close(descriptor)
            except OSError:
                pass
            descriptor = None
            _safe_remove(filename)
            yield False
            return
        yield True
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
            # A stale-lock recovery can replace this path while the original
            # owner is still running.  Only remove the file we created.
            if _lock_owner(filename) == owner:
                _safe_remove(filename)


@contextmanager
def _static_lock():
    profile_root = _profile_root()
    lock_root = os.path.join(profile_root, "locks") if profile_root else None
    with _lock(lock_root, "static") as locked:
        yield locked


@contextmanager
def _refresh_lock(account_hash):
    root = _cache_root(account_hash)
    lock_root = os.path.join(root, "locks") if root else None
    with _lock(lock_root, "refresh") as locked:
        yield locked


def _l1_key(url, account_hash=None):
    account_hash = active_account_hash() if account_hash is None else account_hash
    if not account_hash or not isinstance(url, str) or not url:
        return None
    material = "\x00".join((CACHE_VERSION, account_hash, url))
    return sha256(material.encode("utf-8")).hexdigest()


def _payload_property(key):
    return CACHE_PREFIX + key + CACHE_PAYLOAD_SUFFIX


def _updated_property(key):
    return CACHE_PREFIX + key + CACHE_UPDATED_SUFFIX


def _index():
    return [key for key in _window_get(CACHE_INDEX).split("|") if key]


def _clear_l1(key):
    _window_clear(_payload_property(key))
    _window_clear(_updated_property(key))


def _remember_l1_key(key):
    keys = [cached_key for cached_key in _index() if cached_key != key]
    keys.append(key)
    while len(keys) > CACHE_ENTRY_LIMIT:
        _clear_l1(keys.pop(0))
    _window_set(CACHE_INDEX, "|".join(keys))


def _encode_xmls(xmls):
    if not xmls:
        return None
    try:
        wrapper = etree.Element(CACHE_WRAPPER_TAG)
        for xml in xmls:
            wrapper.append(deepcopy(xml))
        raw = etree.tostring(wrapper, encoding="utf-8")
    except (AttributeError, TypeError, ValueError):
        return None
    if not raw or len(raw) > CACHE_MAX_RAW_BYTES:
        return None
    encoded = b64encode(zlib.compress(raw)).decode("ascii")
    return encoded if len(encoded) <= CACHE_MAX_ENCODED_BYTES else None


def _decode_xmls(encoded):
    if not isinstance(encoded, str) or not encoded or len(encoded) > CACHE_MAX_ENCODED_BYTES:
        return None
    try:
        compressed = b64decode(encoded.encode("ascii"), validate=True)
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, CACHE_MAX_RAW_BYTES + 1)
        if (
            len(raw) > CACHE_MAX_RAW_BYTES
            or decompressor.unconsumed_tail
            or not decompressor.eof
            or decompressor.unused_data
        ):
            return None
        raw += decompressor.flush()
        if len(raw) > CACHE_MAX_RAW_BYTES:
            return None
        root = etree.fromstring(raw)
    except (BinasciiError, TypeError, ValueError, zlib.error, etree.ParseError):
        return None
    if root.tag != CACHE_WRAPPER_TAG:
        return None
    return list(root)


def _l1_stored_at(url, account_hash=None):
    """Read only the L1 timestamp without decompressing the source payload."""
    key = _l1_key(url, account_hash)
    if key is None:
        return None
    try:
        return float(_window_get(_updated_property(key)))
    except (TypeError, ValueError):
        return None


def _load_l1(url, account_hash=None):
    key = _l1_key(url, account_hash)
    stored_at = _l1_stored_at(url, account_hash)
    if key is None or stored_at is None:
        return None, None
    xmls = _decode_xmls(_window_get(_payload_property(key)))
    if xmls is None:
        _clear_l1(key)
        return None, None
    return stored_at, xmls


def _store_l1(url, xmls, stored_at=None, account_hash=None):
    key = _l1_key(url, account_hash)
    encoded = _encode_xmls(xmls)
    if key is None or encoded is None or len(encoded) > CACHE_L1_MAX_ENCODED_BYTES:
        return False
    if not _window_set(_payload_property(key), encoded):
        return False
    _window_set(_updated_property(key), str(stored_at or time()))
    _remember_l1_key(key)
    return True


def _source_path(url, account_hash=None):
    directory = _directory("entries", account_hash)
    if not directory or not isinstance(url, str) or not url:
        return None
    return os.path.join(directory, "%s.json" % sha256(url.encode("utf-8")).hexdigest())


def _overlay_path(rating_key, account_hash=None):
    directory = _directory("overlays", account_hash)
    rating_key = plex_discover.normalize_rating_key(rating_key)
    if not directory or rating_key is None:
        return None
    return os.path.join(directory, "%s.json" % sha256(rating_key.encode("utf-8")).hexdigest())


def _refresh_path(url, cache_kind, account_hash=None):
    directory = _directory("refresh", account_hash)
    if not directory or not isinstance(url, str) or not url:
        return None
    material = "%s\x00%s" % (cache_kind, url)
    return os.path.join(directory, "%s.json" % sha256(material.encode("utf-8")).hexdigest())


def _record_timestamp(record):
    for key in ("last_accessed_at", "stored_at", "created_at"):
        try:
            return float(record[key])
        except (KeyError, TypeError, ValueError):
            continue
    return time()


def _write_json(filename, record):
    """Atomically replace one record, keeping its logical access timestamp."""
    directory = os.path.dirname(filename) if filename else None
    if not _ensure_directory(directory):
        return False
    temporary = "%s.%s.%s.tmp" % (filename, os.getpid(), uuid4().hex)
    try:
        with open(temporary, "w", encoding="utf-8") as cache_file:
            json.dump(record, cache_file, separators=(",", ":"), sort_keys=True)
            cache_file.flush()
            os.fsync(cache_file.fileno())
        timestamp = _record_timestamp(record)
        try:
            os.utime(temporary, (timestamp, timestamp))
        except OSError:
            pass
        os.replace(temporary, filename)
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
        return True
    except (OSError, TypeError, ValueError) as err:
        LOG.debug("Could not persist Plex Discover cache record %s: %s", filename, err)
        _safe_remove(temporary)
        return False


def _read_json(filename):
    if not filename:
        return None
    try:
        with open(filename, "r", encoding="utf-8") as cache_file:
            record = json.load(cache_file)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _freshness(stored_at, fresh_seconds, stale_seconds):
    try:
        age = max(0.0, time() - float(stored_at))
    except (TypeError, ValueError):
        return CACHE_MISS
    if age <= fresh_seconds:
        return CACHE_FRESH
    if age <= stale_seconds:
        return CACHE_STALE
    return CACHE_MISS


def _policy(cache_kind):
    return CACHE_POLICIES.get(cache_kind)


def _load_source_record(filename, cache_kind, account_hash):
    record = _read_json(filename)
    if (
        record is None
        or record.get("schema") != RECORD_SCHEMA_VERSION
        or record.get("kind") != cache_kind
        or record.get("account") != account_hash
    ):
        return None
    try:
        float(record["stored_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return record


def _record_freshness(filename, record, fresh_seconds):
    stored_at = record.get("stored_at")
    try:
        last_accessed = max(float(stored_at), _safe_mtime(filename, 0))
    except (TypeError, ValueError):
        return CACHE_MISS
    if max(0.0, time() - last_accessed) > CACHE_RETENTION_SECONDS:
        return CACHE_MISS
    return (
        CACHE_FRESH
        if _freshness(stored_at, fresh_seconds, fresh_seconds) == CACHE_FRESH
        else CACHE_STALE
    )


def _touch_record(filename):
    now = time()
    previous = _safe_mtime(filename, 0)
    if now - previous < CACHE_TOUCH_INTERVAL_SECONDS:
        return
    try:
        os.utime(filename, (now, now))
    except OSError:
        pass


def read_xml(url, cache_kind, item_key=None):
    """Return a fresh, stale, or missed provider response."""
    del item_key  # Detail URLs are already exact per-item cache keys.
    policy = _policy(cache_kind)
    account_hash = active_account_hash()
    if policy is None or account_hash is None:
        return CACHE_MISS, None
    try:
        l1_stored_at, l1_xmls = _load_l1(url, account_hash)
        if (
            l1_xmls is not None
            and _freshness(l1_stored_at, policy["fresh"], policy["fresh"])
            == CACHE_FRESH
            and account_matches(account_hash)
        ):
            return CACHE_FRESH, l1_xmls

        filename = _source_path(url, account_hash)
        record = _load_source_record(filename, cache_kind, account_hash)
        if record is None:
            return CACHE_MISS, None
        freshness = _record_freshness(filename, record, policy["fresh"])
        if freshness == CACHE_MISS:
            return CACHE_MISS, None
        xmls = _decode_xmls(record.get("payload"))
        if xmls is None:
            return CACHE_MISS, None
        if not account_matches(account_hash):
            return CACHE_MISS, None
        _touch_record(filename)
        _store_l1(url, xmls, record.get("stored_at"), account_hash)
        return freshness, xmls
    except Exception as err:
        LOG.debug("Could not read Plex Discover cache for %s: %s", url, err)
        return CACHE_MISS, None


def get_xml(url, max_age_seconds):
    """Compatibility API returning only a fresh source response, if present."""
    try:
        max_age_seconds = float(max_age_seconds)
    except (TypeError, ValueError):
        return None
    if max_age_seconds <= 0:
        return None
    account_hash = active_account_hash()
    l1_stored_at, l1_xmls = _load_l1(url, account_hash)
    if (
        l1_xmls is not None
        and _freshness(l1_stored_at, max_age_seconds, max_age_seconds) == CACHE_FRESH
        and account_matches(account_hash)
    ):
        return l1_xmls
    return None


def source_is_current(url, max_age_seconds=WATCHLIST_OVERLAY_SECONDS):
    """Whether a lightweight L1 timestamp keeps embedded userState current.

    This runs while creating every list item in a hub.  It must never decode
    the full XML response or JSON-load its durable payload on that hot path.
    A source without an L1 timestamp simply relies on the verified Watchlist
    overlay/status worker instead of its embedded provider hint.
    """
    try:
        max_age_seconds = float(max_age_seconds)
    except (TypeError, ValueError):
        return False
    if max_age_seconds <= 0:
        return False
    account_hash = active_account_hash()
    l1_stored_at = _l1_stored_at(url, account_hash)
    if (
        l1_stored_at is not None
        and _freshness(l1_stored_at, max_age_seconds, max_age_seconds)
        == CACHE_FRESH
        and account_matches(account_hash)
    ):
        return True
    return False


def _payload_record(encoded, stored_at, cache_kind, account_hash):
    return {
        "schema": RECORD_SCHEMA_VERSION,
        "record_id": uuid4().hex,
        "account": account_hash,
        "stored_at": stored_at,
        "last_accessed_at": stored_at,
        "kind": cache_kind,
        "payload": encoded,
    }


def put_xml(url, xmls, cache_kind=None, account_hash=None):
    """Persist one bounded source response without delaying card rendering."""
    policy = _policy(cache_kind)
    account_hash = active_account_hash() if account_hash is None else account_hash
    if policy is None or not account_matches(account_hash):
        return False
    encoded = _encode_xmls(xmls)
    if encoded is None:
        return False
    stored_at = time()
    filename = _source_path(url, account_hash)
    if filename is None:
        return False
    try:
        with _static_lock() as locked:
            if not locked or not account_matches(account_hash):
                return False
            stored = _write_json(
                filename,
                _payload_record(encoded, stored_at, cache_kind, account_hash),
            )
            if stored:
                _store_l1(url, xmls, stored_at, account_hash)
                _request_maintenance()
            return stored
    except Exception as err:
        LOG.debug("Could not store Plex Discover cache for %s: %s", url, err)
        return False


def _overlay_properties(rating_key, account_hash=None):
    account_hash = active_account_hash() if account_hash is None else account_hash
    rating_key = plex_discover.normalize_rating_key(rating_key)
    if rating_key is None or not _valid_account_hash(account_hash):
        return None, None
    digest = sha256(
        ("%s\x00%s" % (account_hash, rating_key)).encode("utf-8")
    ).hexdigest()
    return (
        CACHE_OVERLAY_PREFIX + digest + ".State",
        CACHE_OVERLAY_PREFIX + digest + ".Updated",
    )


def record_watchlist_state(rating_key, present, account_hash=None):
    """Persist one verified Watchlist state without flushing Discover content."""
    account_hash = active_account_hash() if account_hash is None else account_hash
    rating_key = plex_discover.normalize_rating_key(rating_key)
    if (
        rating_key is None
        or present not in (True, False, "present", "absent")
        or not account_matches(account_hash)
    ):
        return False
    state = "present" if present is True or present == "present" else "absent"
    stored_at = time()
    state_property, updated_property = _overlay_properties(rating_key, account_hash)
    if state_property:
        _window_set(state_property, state)
        _window_set(updated_property, str(stored_at))
    filename = _overlay_path(rating_key, account_hash)
    if filename is None:
        return state_property is not None
    record = {
        "schema": RECORD_SCHEMA_VERSION,
        "record_id": uuid4().hex,
        "account": account_hash,
        "rating_key": rating_key,
        "state": state,
        "stored_at": stored_at,
        "last_accessed_at": stored_at,
    }
    try:
        with _static_lock() as locked:
            if not locked:
                return state_property is not None
            stored = _write_json(filename, record)
            if stored:
                _request_maintenance()
            return stored or state_property is not None
    except Exception as err:
        LOG.debug("Could not store Plex Watchlist overlay: %s", err)
        return state_property is not None


def watchlist_hint(rating_key, fallback=None):
    """Prefer a recent verified overlay to a recent embedded provider hint."""
    account_hash = active_account_hash()
    rating_key = plex_discover.normalize_rating_key(rating_key)
    if rating_key is None or account_hash is None:
        return fallback
    state_property, updated_property = _overlay_properties(rating_key, account_hash)
    try:
        updated = float(_window_get(updated_property)) if updated_property else None
    except (TypeError, ValueError):
        updated = None
    state = _window_get(state_property) if state_property else None
    if (
        state in ("present", "absent")
        and updated is not None
        and _freshness(updated, WATCHLIST_OVERLAY_SECONDS, WATCHLIST_OVERLAY_SECONDS)
        == CACHE_FRESH
    ):
        return state

    record = _read_json(_overlay_path(rating_key, account_hash))
    if (
        record is None
        or record.get("schema") != RECORD_SCHEMA_VERSION
        or record.get("account") != account_hash
        or record.get("rating_key") != rating_key
    ):
        return fallback
    state = record.get("state")
    if state not in ("present", "absent"):
        return fallback
    if (
        _freshness(
            record.get("stored_at"),
            WATCHLIST_OVERLAY_SECONDS,
            WATCHLIST_OVERLAY_SECONDS,
        )
        != CACHE_FRESH
    ):
        return fallback
    if state_property:
        _window_set(state_property, state)
        _window_set(updated_property, str(record.get("stored_at")))
    return state


def refresh_hosts(cache_kind):
    return REFRESH_HOSTS.get(cache_kind, ())


def _normalize_refresh_priority(priority):
    try:
        priority = int(priority)
    except (TypeError, ValueError):
        return None
    return (
        priority
        if REFRESH_PRIORITY_FOREGROUND <= priority <= REFRESH_PRIORITY_PREFETCH
        else None
    )


def _request_refresh():
    _window_set(CACHE_REFRESH_REQUESTED, str(time()))


def refresh_requested_at():
    """Return the latest in-memory refresh wake signal for the service."""
    return _window_timestamp(CACHE_REFRESH_REQUESTED)


def _valid_refresh_url(url, cache_kind):
    hosts = {host.lower() for host in refresh_hosts(cache_kind)}
    if not isinstance(url, str) or not hosts:
        return False
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.lower() != "https"
        or hostname not in hosts
        or parsed.username
        or parsed.password
        or port is not None
    ):
        return False
    if cache_kind == "catalog":
        return parsed.path == "/hubs/sections/home"
    if cache_kind == "hub":
        return parsed.path.startswith("/hubs/sections/home/")
    if cache_kind == "detail":
        rating_key = parsed.path.rsplit("/", 1)[-1]
        return parsed.path.startswith("/library/metadata/") and (
            plex_discover.normalize_rating_key(rating_key) is not None
        )
    return False


def request_hub_prefetch(urls):
    """Hand a few likely hub URLs to the idle service without foreground I/O."""
    account_hash = active_account_hash()
    if account_hash is None:
        return False
    unique_urls = []
    for url in urls or ():
        if (
            isinstance(url, str)
            and url not in unique_urls
            and _valid_refresh_url(url, "hub")
        ):
            unique_urls.append(url)
        if len(unique_urls) >= PREFETCH_HUB_LIMIT:
            break
    if not unique_urls:
        return False
    try:
        payload = json.dumps(
            {"account": account_hash, "urls": unique_urls},
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return False
    if not _window_set(CACHE_PREFETCH_REQUEST, payload):
        return False
    _request_refresh()
    return True


def claim_hub_prefetch():
    """Consume the current account's non-durable catalog prefetch request."""
    payload = _window_get(CACHE_PREFETCH_REQUEST)
    _window_clear(CACHE_PREFETCH_REQUEST)
    account_hash = active_account_hash()
    try:
        request = json.loads(payload)
    except (TypeError, ValueError):
        return ()
    if (
        not isinstance(request, dict)
        or request.get("account") != account_hash
        or not account_matches(account_hash)
        or not isinstance(request.get("urls"), list)
    ):
        return ()
    return tuple(
        url
        for url in request["urls"][:PREFETCH_HUB_LIMIT]
        if _valid_refresh_url(url, "hub")
    )


def _valid_refresh_record(record, account_hash):
    if (
        not isinstance(record, dict)
        or record.get("schema") != RECORD_SCHEMA_VERSION
        or record.get("account") != account_hash
        or not _valid_refresh_url(record.get("url"), record.get("kind"))
    ):
        return None
    try:
        created_at = float(record["created_at"])
        not_before = float(record["not_before"])
        attempt = int(record.get("attempt", 0))
        priority = _normalize_refresh_priority(
            record.get("priority", REFRESH_PRIORITY_FOREGROUND)
        )
    except (KeyError, TypeError, ValueError):
        return None
    if attempt < 0 or attempt > 8 or priority is None:
        return None
    result = dict(record)
    result["created_at"] = created_at
    result["not_before"] = not_before
    result["attempt"] = attempt
    result["priority"] = priority
    return result


def _claim_path(queue, claimed_at):
    return "%s.%d.%s.claim" % (queue[: -len(".json")], int(claimed_at), uuid4().hex)


def _queue_path_from_claim(claim):
    directory = os.path.dirname(claim)
    name = os.path.basename(claim)
    if not name.endswith(".claim"):
        return None, None
    stem = name[: -len(".claim")]
    parts = stem.split(".")
    if len(parts) == 3 and len(parts[0]) == 64:
        try:
            claimed_at = float(parts[1])
        except ValueError:
            claimed_at = _safe_mtime(claim, 0)
        return os.path.join(directory, "%s.json" % parts[0]), claimed_at
    # Compatibility with an interrupted cache created before lease filenames.
    return os.path.join(directory, "%s.json" % stem), _safe_mtime(claim, 0)


def _cleanup_refresh_temps(directory):
    removed = 0
    for name in _listdir(directory):
        if not name.endswith(".tmp"):
            continue
        filename = os.path.join(directory, name)
        if time() - _safe_mtime(filename, time()) > CACHE_TEMP_RETENTION_SECONDS:
            removed += int(_safe_remove(filename))
    return removed


def enqueue_refresh(url, cache_kind, priority=REFRESH_PRIORITY_FOREGROUND):
    """Queue one validated refresh; a live claim may receive one dirty requeue."""
    account_hash = active_account_hash()
    priority = _normalize_refresh_priority(priority)
    if (
        account_hash is None
        or priority is None
        or not _valid_refresh_url(url, cache_kind)
    ):
        return False
    queue = _refresh_path(url, cache_kind, account_hash)
    directory = _directory("refresh", account_hash)
    if queue is None or directory is None:
        return False
    try:
        with _refresh_lock(account_hash) as locked:
            if not locked:
                return False
            _cleanup_refresh_temps(directory)
            if os.path.exists(queue):
                existing = _valid_refresh_record(_read_json(queue), account_hash)
                if existing is not None:
                    if priority >= existing["priority"]:
                        return True
                    existing["priority"] = priority
                    existing["record_id"] = uuid4().hex
                    if _write_json(queue, existing):
                        _request_refresh()
                        return True
                    return False
                _safe_remove(queue)
            pending = [
                name
                for name in _listdir(directory)
                if name.endswith(".json") or name.endswith(".claim")
            ]
            if len(pending) >= REFRESH_REQUEST_LIMIT:
                return False
            now = time()
            queued = _write_json(
                queue,
                {
                    "schema": RECORD_SCHEMA_VERSION,
                    "record_id": uuid4().hex,
                    "account": account_hash,
                    "url": url,
                    "kind": cache_kind,
                    "created_at": now,
                    "not_before": now,
                    "attempt": 0,
                    "priority": priority,
                },
            )
            if queued:
                _request_refresh()
            return queued
    except Exception as err:
        LOG.debug("Could not enqueue Plex Discover refresh: %s", err)
        return False


def _recover_refresh_claims_locked(account_hash, directory):
    """Recover expired claims while the account refresh lock is held."""
    recovered = 0
    _cleanup_refresh_temps(directory)
    now = time()
    for name in _listdir(directory):
        if not name.endswith(".claim"):
            continue
        claim = os.path.join(directory, name)
        queue, claimed_at = _queue_path_from_claim(claim)
        request = _valid_refresh_record(_read_json(claim), account_hash)
        if request is None:
            recovered += int(_safe_remove(claim))
            continue
        if max(0.0, now - claimed_at) < REFRESH_LEASE_SECONDS:
            continue
        if queue is None or os.path.exists(queue):
            recovered += int(_safe_remove(claim))
            continue
        try:
            os.replace(claim, queue)
            recovered += 1
        except OSError:
            pass
    return recovered


def recover_refresh_claims():
    """Recover only expired claims; another live Kodi service keeps its lease."""
    account_hash = active_account_hash()
    directory = _directory("refresh", account_hash)
    if account_hash is None or directory is None:
        return 0
    try:
        with _refresh_lock(account_hash) as locked:
            return _recover_refresh_claims_locked(account_hash, directory) if locked else 0
    except Exception as err:
        LOG.debug("Could not recover Plex Discover refresh claims: %s", err)
        return 0


def claim_refresh():
    """Atomically claim one due refresh request for the active account."""
    account_hash = active_account_hash()
    directory = _directory("refresh", account_hash)
    if account_hash is None or directory is None:
        return None
    try:
        with _refresh_lock(account_hash) as locked:
            if not locked:
                return None
            _recover_refresh_claims_locked(account_hash, directory)
            queues = [
                os.path.join(directory, name)
                for name in _listdir(directory)
                if name.endswith(".json")
            ]
            candidates = []
            now = time()
            for queue in queues[:REFRESH_REQUEST_LIMIT]:
                request = _valid_refresh_record(_read_json(queue), account_hash)
                if request is None:
                    _safe_remove(queue)
                    continue
                if now - request["created_at"] > REFRESH_REQUEST_RETENTION_SECONDS:
                    _safe_remove(queue)
                    continue
                if request["not_before"] > now:
                    continue
                candidates.append(
                    (
                        request["priority"],
                        _safe_mtime(queue, float("inf")),
                        queue,
                        request,
                    )
                )
            for _, _, queue, request in sorted(candidates):
                claim = _claim_path(queue, now)
                try:
                    os.replace(queue, claim)
                except OSError:
                    continue
                request["_account_hash"] = account_hash
                request["_queue_path"] = queue
                request["_claim_path"] = claim
                return request
    except Exception as err:
        LOG.debug("Could not claim Plex Discover refresh: %s", err)
    return None


def _refresh_request_paths(request):
    account_hash = request.get("_account_hash") if isinstance(request, dict) else None
    directory = _directory("refresh", account_hash)
    queue = request.get("_queue_path") if isinstance(request, dict) else None
    claim = request.get("_claim_path") if isinstance(request, dict) else None
    if not directory or not queue or not claim:
        return None, None, None
    directory = os.path.abspath(directory)
    if (
        os.path.abspath(os.path.dirname(queue)) != directory
        or os.path.abspath(os.path.dirname(claim)) != directory
        or not queue.endswith(".json")
        or not claim.endswith(".claim")
    ):
        return None, None, None
    return account_hash, queue, claim


def finish_refresh(request, success):
    """Acknowledge a claim, retaining it if its retry cannot be persisted.

    The caller is the low-priority service worker, so a few very short lock
    retries are preferable to leaving a completed claim until its lease
    expires.  The claim remains recoverable if the cache directory is truly
    unavailable.
    """
    account_hash, queue, claim = _refresh_request_paths(request)
    if account_hash is None:
        return False
    for lock_attempt in range(REFRESH_FINISH_ATTEMPTS):
        try:
            with _refresh_lock(account_hash) as locked:
                if not locked:
                    continue
                if success:
                    return _safe_remove(claim)
                if os.path.exists(queue):
                    _safe_remove(claim)
                    return True
                retry = dict(request)
                retry.pop("_account_hash", None)
                retry.pop("_queue_path", None)
                retry.pop("_claim_path", None)
                try:
                    attempt = int(retry.get("attempt", 0))
                except (TypeError, ValueError):
                    attempt = 0
                retry["attempt"] = min(max(attempt, 0) + 1, 8)
                retry["not_before"] = time() + min(
                    2 ** retry["attempt"], REFRESH_RETRY_LIMIT_SECONDS
                )
                retry["account"] = account_hash
                retry["record_id"] = uuid4().hex
                if not _write_json(queue, retry):
                    return False
                _safe_remove(claim)
                return True
        except Exception as err:
            LOG.debug("Could not finish Plex Discover refresh: %s", err)
            return False
        if lock_attempt + 1 < REFRESH_FINISH_ATTEMPTS:
            sleep(REFRESH_FINISH_RETRY_SECONDS)
    return False


def _namespace_hashes():
    return [
        name for name in _listdir(_profile_root()) if _valid_account_hash(name)
    ]


def _window_timestamp(key):
    try:
        return float(_window_get(key))
    except (TypeError, ValueError):
        return 0.0


def _request_maintenance():
    """Ask the service worker to enforce retention outside a listing process."""
    _window_set(CACHE_MAINTENANCE_REQUESTED, str(time()))


def maintenance_due():
    """Return whether background cache housekeeping should be scheduled."""
    if _profile_root() is None:
        return False
    requested_at = _window_timestamp(CACHE_MAINTENANCE_REQUESTED)
    completed_at = _window_timestamp(CACHE_MAINTENANCE_COMPLETED)
    now = time()
    return requested_at > completed_at or (
        completed_at <= 0
        or max(0.0, now - completed_at) >= CACHE_MAINTENANCE_INTERVAL_SECONDS
    )


def _cleanup_static_temps(directory):
    removed = 0
    for name in _listdir(directory):
        if not name.endswith(".tmp"):
            continue
        filename = os.path.join(directory, name)
        if time() - _safe_mtime(filename, time()) > CACHE_TEMP_RETENTION_SECONDS:
            removed += int(_safe_remove(filename))
    return removed


def _sweep_static_locked():
    """Purge inactive namespace data and enforce global source/disk limits."""
    now = time()
    removed = 0
    source_records = []
    static_records = []
    for account_hash in _namespace_hashes():
        for directory_name in ("entries", "overlays"):
            directory = _directory(directory_name, account_hash)
            if not directory:
                continue
            removed += _cleanup_static_temps(directory)
            for name in _listdir(directory):
                if not name.endswith(".json"):
                    continue
                filename = os.path.join(directory, name)
                record = _read_json(filename)
                if (
                    record is None
                    or record.get("schema") != RECORD_SCHEMA_VERSION
                    or record.get("account") != account_hash
                ):
                    removed += int(_safe_remove(filename))
                    continue
                stored_at = _record_timestamp(record)
                last_accessed = max(stored_at, _safe_mtime(filename, 0))
                if now - last_accessed > CACHE_RETENTION_SECONDS:
                    removed += int(_safe_remove(filename))
                    continue
                try:
                    size = os.path.getsize(filename)
                except OSError:
                    continue
                record_info = (last_accessed, size, filename)
                static_records.append(record_info)
                if directory_name == "entries":
                    source_records.append(record_info)

    for _, _, filename in sorted(source_records)[: max(
        0, len(source_records) - CACHE_MAX_SOURCE_RECORDS
    )]:
        if _safe_remove(filename):
            removed += 1
            static_records = [record for record in static_records if record[2] != filename]

    total_size = sum(size for _, size, _ in static_records)
    for _, size, filename in sorted(static_records):
        if total_size <= CACHE_MAX_DISK_BYTES:
            break
        if _safe_remove(filename):
            total_size -= size
            removed += 1
    return removed


def sweep():
    """Run bounded retention/cap cleanup across every account namespace."""
    try:
        with _static_lock() as locked:
            if not locked:
                return 0
            removed = _sweep_static_locked()
            _window_set(CACHE_MAINTENANCE_COMPLETED, str(time()))
            return removed
    except Exception as err:
        LOG.debug("Could not sweep Plex Discover cache: %s", err)
        return 0


def invalidate():
    """Clear only volatile Home-window state; durable cache remains reusable."""
    for key in _index():
        _clear_l1(key)
    _window_clear(CACHE_INDEX)
