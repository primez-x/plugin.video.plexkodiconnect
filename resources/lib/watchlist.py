#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Native Plex Watchlist operations shared by PKC entry points and service."""

from logging import getLogger
import threading
from time import time
from uuid import uuid4

import xbmc

from . import app, clientinfo, downloadutils, plex_discover, utils


LOG = getLogger("PLEX.watchlist")

WATCHLIST_USER_STATE_URL = (
    "https://metadata.provider.plex.tv/library/metadata/%s/userState"
)
WATCHLIST_VERIFY_ATTEMPTS = 4
WATCHLIST_VERIFY_SLEEP_MS = 250
WATCHLIST_HTTP_TIMEOUT = (3.0, 8.0)
WATCHLIST_MONITOR_ATTEMPTS = 25
WATCHLIST_MONITOR_SLEEP_MS = 200
WATCHLIST_STATE_CACHE_SECONDS = 300

DETAIL_IDENTITY = "PKC.Watchlist.Detail.Identity"
DETAIL_STATE = "PKC.Watchlist.Detail.State"
DETAIL_PREVIOUS_STATE = "PKC.Watchlist.Detail.PreviousState"
DETAIL_PENDING = "PKC.Watchlist.Detail.Pending"
DETAIL_REQUEST_ID = "PKC.Watchlist.Detail.RequestId"
DETAIL_REVISION = "PKC.Watchlist.Detail.Revision"
DETAIL_STATUS_REVISION = "PKC.Watchlist.Detail.StatusRevision"
DETAIL_ERROR = "PKC.Watchlist.Detail.Error"
DETAIL_CONTEXT_LABEL = "PKC.Watchlist.Detail.Context.Label"
DETAIL_CONTEXT_DBTYPE = "PKC.Watchlist.Detail.Context.DBType"
DETAIL_TMDB_ID = "PKC.Watchlist.Detail.TMDbId"
DETAIL_TMDB_TYPE = "PKC.Watchlist.Detail.TMDbType"
DETAIL_RATING_KEY = "PKC.Watchlist.Detail.RatingKey"
DETAIL_MONITOR_REVISION = "PKC.Watchlist.Detail.MonitorRevision"
ITEM_STATE_PREFIX = "PKC.Watchlist.Item."

TMDB_MONITOR_ID = "TMDbHelper.ListItem.Monitor.TMDb_ID"
TMDB_MONITOR_TYPE = "TMDbHelper.ListItem.Monitor.TMDb_Type"
TMDB_MONITOR_LABEL = "TMDbHelper.ListItem.base_label"
TMDB_MONITOR_DBTYPE = "TMDbHelper.ListItem.base_dbtype"

LOCKS = {}
LOCKS_LOCK = threading.Lock()


def _ensure_runtime():
    """Initialise only the lightweight connection state in a plugin process."""
    if getattr(app, "CONN", None) is None:
        app.init(entrypoint=True)


def _headers(accept_json=False):
    headers = {"X-Plex-Token": utils.window("plex_token")}
    if accept_json:
        headers["Accept"] = "application/json"
    return clientinfo.getXArgsDeviceInfo(headers, include_token=False)


def discover_tmdb_ratingkey(tmdb_id, tmdb_type):
    """Resolve an exact TMDb identity to Plex's canonical opaque key."""
    parameters = plex_discover.tmdb_match_parameters(tmdb_id, tmdb_type)
    if parameters is None:
        return None
    _ensure_runtime()
    response = downloadutils.DownloadUtils().downloadUrl(
        plex_discover.METADATA_MATCH_URL,
        parameters=parameters,
        authenticate=False,
        headerOptions=_headers(accept_json=True),
        return_response=True,
        timeout=WATCHLIST_HTTP_TIMEOUT,
    )
    if response is None or not getattr(response, "ok", False):
        LOG.warning(
            "discover_tmdb_ratingkey: Plex metadata match returned HTTP %s",
            getattr(response, "status_code", "unknown"),
        )
        return None
    try:
        payload = response.json()
    except (TypeError, ValueError):
        LOG.warning("discover_tmdb_ratingkey: Plex metadata match returned invalid JSON")
        return None
    match = plex_discover.resolve_tmdb_match(payload, tmdb_type)
    if match is None:
        LOG.warning("discover_tmdb_ratingkey: Plex returned no unique exact match")
        return None
    return match["rating_key"]


def _rating_key_for_tmdb(params, identity):
    rating_key = _cached_rating_key(identity)
    if rating_key is not None:
        return rating_key
    rating_key = discover_tmdb_ratingkey(
        params.get("tmdb_id"), params.get("tmdb_type") or params.get("plex_type")
    )
    if rating_key is not None:
        _remember_rating_key(identity, rating_key)
    return rating_key


def state(rating_key):
    """Read exact Watchlist membership from Plex's canonical userState API."""
    rating_key = plex_discover.normalize_rating_key(rating_key)
    if rating_key is None:
        LOG.warning("watchlist state requested with an invalid rating key")
        return None
    _ensure_runtime()
    response = downloadutils.DownloadUtils().downloadUrl(
        WATCHLIST_USER_STATE_URL % rating_key,
        authenticate=False,
        headerOptions=_headers(accept_json=True),
        return_response=True,
        timeout=WATCHLIST_HTTP_TIMEOUT,
    )
    if response is None or not getattr(response, "ok", False):
        LOG.warning(
            "Plex Watchlist user state returned HTTP %s",
            getattr(response, "status_code", "unknown"),
        )
        return None
    try:
        payload = response.json()
    except (TypeError, ValueError):
        LOG.warning("Plex Watchlist user state returned invalid JSON")
        return None
    return plex_discover.watchlist_state_from_payload(payload, rating_key)


def _action(api_type, rating_key):
    _ensure_runtime()
    response = downloadutils.DownloadUtils().downloadUrl(
        "https://discover.provider.plex.tv/actions/%s" % api_type,
        action_type="PUT",
        parameters={"ratingKey": rating_key},
        authenticate=False,
        headerOptions=_headers(),
        return_response=True,
        timeout=WATCHLIST_HTTP_TIMEOUT,
    )
    if response is None or not getattr(response, "ok", False):
        LOG.warning(
            "Plex Watchlist %s returned HTTP %s",
            api_type,
            getattr(response, "status_code", "unknown"),
        )
        return False
    return True


def _lock(rating_key):
    with LOCKS_LOCK:
        return LOCKS.setdefault(rating_key, threading.Lock())


def _desired_state(api_type):
    return {
        "addToWatchlist": True,
        "removeFromWatchlist": False,
    }.get(api_type)


def change(api_type, rating_key):
    """Set an absolute Watchlist state and verify it before reporting success."""
    rating_key = plex_discover.normalize_rating_key(rating_key)
    desired_state = _desired_state(api_type)
    if rating_key is None or desired_state is None:
        return False, None

    with _lock(rating_key):
        current_state = state(rating_key)
        if current_state is desired_state:
            return True, current_state

        action_acknowledged = _action(api_type, rating_key)
        observed_state = None
        for attempt in range(WATCHLIST_VERIFY_ATTEMPTS):
            observed_state = state(rating_key)
            if observed_state is desired_state:
                return True, observed_state
            if attempt + 1 < WATCHLIST_VERIFY_ATTEMPTS:
                xbmc.sleep(WATCHLIST_VERIFY_SLEEP_MS)

        LOG.warning(
            "Plex Watchlist %s was not verified for rating key %s "
            "(HTTP acknowledged: %s, observed: %s)",
            api_type,
            rating_key,
            action_acknowledged,
            observed_state,
        )
        return False, observed_state


def notify_error(message="Plex Watchlist could not be updated."):
    """Show feedback only after a verified failure."""
    utils.dialog(
        "notification",
        utils.lang(29999),
        message,
        icon="{error}",
        time=3500,
        sound=False,
    )


def _item_state_property(identity):
    return "%s%s.State" % (ITEM_STATE_PREFIX, identity)


def _item_state_timestamp_property(identity):
    return "%s%s.StateUpdated" % (ITEM_STATE_PREFIX, identity)


def _item_rating_key_property(identity):
    return "%s%s.RatingKey" % (ITEM_STATE_PREFIX, identity)


def _remember_state(identity, state_name):
    if identity is None or state_name not in ("present", "absent"):
        return
    utils.window(_item_state_property(identity), value=state_name)
    utils.window(_item_state_timestamp_property(identity), value=str(time()))


def _cached_state(identity):
    if identity is None:
        return None
    state_name = utils.window(_item_state_property(identity))
    if state_name not in ("present", "absent"):
        return None
    try:
        updated = float(utils.window(_item_state_timestamp_property(identity)))
    except (TypeError, ValueError):
        return None
    if time() - updated > WATCHLIST_STATE_CACHE_SECONDS:
        return None
    return state_name


def _remember_rating_key(identity, rating_key):
    rating_key = plex_discover.normalize_rating_key(rating_key)
    if identity is None or rating_key is None:
        return
    utils.window(_item_rating_key_property(identity), value=rating_key)
    if utils.window(DETAIL_IDENTITY) == identity:
        utils.window(DETAIL_RATING_KEY, value=rating_key)


def _cached_rating_key(identity):
    if identity is None:
        return None
    rating_key = plex_discover.normalize_rating_key(utils.window(DETAIL_RATING_KEY))
    if utils.window(DETAIL_IDENTITY) == identity and rating_key is not None:
        return rating_key
    return plex_discover.normalize_rating_key(utils.window(_item_rating_key_property(identity)))


def _identity_for_key(params):
    rating_key = plex_discover.normalize_rating_key(params.get("rating_key"))
    return "plex.%s" % rating_key if rating_key else None


def _identity_for_tmdb(params):
    return plex_discover.tmdb_watchlist_identity(
        params.get("tmdb_id"), params.get("tmdb_type") or params.get("plex_type")
    )


def _known_state(identity):
    value = _cached_state(identity)
    if value is not None:
        return value
    if utils.window(DETAIL_IDENTITY) == identity:
        value = utils.window(DETAIL_STATE)
        if value in ("present", "absent"):
            return value
    return "absent"


def _begin(identity, desired):
    if utils.window(DETAIL_IDENTITY) != identity:
        return None, None
    previous_state = _known_state(identity)
    request_id = uuid4().hex
    # Set this first so no status worker can interleave between revision/state
    # projection and leave this action stranded in a pending state.
    utils.window(DETAIL_PENDING, value=request_id)
    if utils.window(DETAIL_IDENTITY) != identity:
        if utils.window(DETAIL_PENDING) == request_id:
            utils.window(DETAIL_PENDING, clear=True)
        return None, None
    utils.window(DETAIL_IDENTITY, value=identity)
    utils.window(DETAIL_PREVIOUS_STATE, value=previous_state)
    utils.window(DETAIL_REQUEST_ID, value=request_id)
    utils.window(DETAIL_REVISION, value=request_id)
    utils.window(DETAIL_ERROR, clear=True)
    utils.window(DETAIL_STATE, value=desired)
    return request_id, previous_state


def _state_name(value):
    if value is True:
        return "present"
    if value is False:
        return "absent"
    return None


def _project_mutation(identity, request_id, observed_state):
    state_name = _state_name(observed_state)
    if identity is None or state_name is None:
        return False
    _remember_state(identity, state_name)
    if (
        utils.window(DETAIL_IDENTITY) != identity
        or utils.window(DETAIL_REVISION) != request_id
        or utils.window(DETAIL_REQUEST_ID) != request_id
    ):
        return False
    utils.window(DETAIL_STATE, value=state_name)
    utils.window(DETAIL_ERROR, clear=True)
    utils.window(DETAIL_REQUEST_ID, clear=True)
    # Clear last: this is the skin's double-click guard.
    utils.window(DETAIL_PENDING, clear=True)
    return True


def _project_failure(identity, request_id, observed_state, previous_state):
    confirmed_name = _state_name(observed_state)
    if confirmed_name is not None and identity is not None:
        _remember_state(identity, confirmed_name)
    if (
        not identity
        or not request_id
        or utils.window(DETAIL_IDENTITY) != identity
        or utils.window(DETAIL_REVISION) != request_id
        or utils.window(DETAIL_REQUEST_ID) != request_id
    ):
        return False
    fallback_state = previous_state if previous_state in ("present", "absent") else "absent"
    utils.window(DETAIL_STATE, value=confirmed_name or fallback_state)
    utils.window(DETAIL_ERROR, value="verification-failed")
    utils.window(DETAIL_REQUEST_ID, clear=True)
    # Clear last: this is the skin's double-click guard.
    utils.window(DETAIL_PENDING, clear=True)
    return True


def _set(api_type, params, identity, rating_key):
    if identity is None or rating_key is None:
        notify_error()
        return False
    request_id, previous_state = _begin(identity, _state_name(_desired_state(api_type)))
    success, observed_state = change(api_type, rating_key)
    if request_id is None:
        if not success:
            notify_error()
        return success
    if success:
        _project_mutation(identity, request_id, observed_state)
        return True
    _project_failure(identity, request_id, observed_state, previous_state)
    notify_error()
    return False


def set_key(params, desired):
    """Optimistically update a direct Plex Discover item, then verify it."""
    api_type = {"present": "addToWatchlist", "absent": "removeFromWatchlist"}.get(
        desired
    )
    if api_type is None:
        return False
    rating_key = plex_discover.normalize_rating_key(params.get("rating_key"))
    return _set(api_type, params, _identity_for_key(params), rating_key)


def set_tmdb(params, desired):
    """Optimistically update an exact TMDb item, then verify Plex membership."""
    api_type = {"present": "addToWatchlist", "absent": "removeFromWatchlist"}.get(
        desired
    )
    if api_type is None:
        return False
    identity = _identity_for_tmdb(params)
    if identity is None:
        notify_error("Plex could not uniquely match this TMDb item for Watchlist.")
        return False
    request_id, previous_state = _begin(identity, desired)
    rating_key = _rating_key_for_tmdb(params, identity)
    if rating_key is None:
        if request_id is not None:
            _project_failure(identity, request_id, None, previous_state)
        notify_error("Plex could not uniquely match this TMDb item for Watchlist.")
        return False
    success, observed_state = change(api_type, rating_key)
    if request_id is None:
        if not success:
            notify_error()
        return success
    if success:
        _project_mutation(identity, request_id, observed_state)
        return True
    _project_failure(identity, request_id, observed_state, previous_state)
    notify_error()
    return False


def _capture_status(identity):
    # Capture mutation state before checking Pending. If an action begins
    # between these reads, its revision differs and this stale probe rejects.
    mutation_revision = utils.window(DETAIL_REVISION)
    if (
        utils.window(DETAIL_IDENTITY) != identity
        or utils.window(DETAIL_PENDING)
        or utils.window(DETAIL_REVISION) != mutation_revision
    ):
        return None
    # Status reads get an independent token and snapshot the current mutation
    # revision. They must never overwrite an in-flight or completed mutation.
    status_revision = uuid4().hex
    utils.window(DETAIL_STATUS_REVISION, value=status_revision)
    return status_revision, mutation_revision


def _project_status(identity, status_revision, mutation_revision, observed_state):
    state_name = _state_name(observed_state)
    if (
        state_name is None
        or utils.window(DETAIL_IDENTITY) != identity
        or utils.window(DETAIL_STATUS_REVISION) != status_revision
        or utils.window(DETAIL_REVISION) != mutation_revision
        or utils.window(DETAIL_PENDING)
    ):
        return False
    _remember_state(identity, state_name)
    utils.window(DETAIL_STATE, value=state_name)
    return True


def _project_cached_status(identity, status_revision, mutation_revision):
    cached = _cached_state(identity)
    if cached is None:
        return False
    return _project_status(
        identity, status_revision, mutation_revision, cached == "present"
    )


def _bootstrap_cached_status(identity):
    """Project a local state immediately, without delaying a dialog on I/O."""
    revision = _capture_status(identity)
    if revision is None:
        return False
    return _project_cached_status(identity, revision[0], revision[1])


def bootstrap_status_key(params):
    """Immediately restore a cached state for a direct Plex detail item."""
    identity = params.get("watchlist_identity") or _identity_for_key(params)
    if identity is None:
        return False
    return _bootstrap_cached_status(identity)


def bootstrap_status_tmdb(params):
    """Immediately restore a cached state for a TMDb detail item."""
    identity = params.get("watchlist_identity") or _identity_for_tmdb(params)
    if identity is None:
        return False
    return _bootstrap_cached_status(identity)


def status_key(params):
    """Silently project an authoritative state for the active Discover detail."""
    identity = params.get("watchlist_identity") or _identity_for_key(params)
    rating_key = plex_discover.normalize_rating_key(params.get("rating_key"))
    if identity is None or rating_key is None:
        return False
    revision = _capture_status(identity)
    if revision is None:
        return False
    _project_cached_status(identity, revision[0], revision[1])
    return _project_status(identity, revision[0], revision[1], state(rating_key))


def status_tmdb(params):
    """Silently project an authoritative state for the active TMDb detail."""
    identity = params.get("watchlist_identity") or _identity_for_tmdb(params)
    if identity is None:
        return False
    revision = _capture_status(identity)
    if revision is None:
        return False
    _project_cached_status(identity, revision[0], revision[1])
    rating_key = _rating_key_for_tmdb(params, identity)
    if rating_key is None:
        return False
    return _project_status(identity, revision[0], revision[1], state(rating_key))


def _monitor_context():
    return (
        utils.window(DETAIL_CONTEXT_LABEL),
        utils.window(DETAIL_CONTEXT_DBTYPE),
    )


def status_monitor():
    """Wait for TMDb Helper's current monitor result, never its stale predecessor."""
    expected_label, expected_dbtype = _monitor_context()
    if not expected_label or expected_dbtype not in ("movie", "tvshow"):
        return False
    monitor_revision = uuid4().hex
    utils.window(DETAIL_MONITOR_REVISION, value=monitor_revision)

    for attempt in range(WATCHLIST_MONITOR_ATTEMPTS):
        if (
            utils.window(DETAIL_MONITOR_REVISION) != monitor_revision
            or _monitor_context() != (expected_label, expected_dbtype)
        ):
            return False
        if utils.window(DETAIL_IDENTITY) or utils.window(DETAIL_PENDING):
            return False
        tmdb_id = utils.window(TMDB_MONITOR_ID)
        tmdb_type = utils.window(TMDB_MONITOR_TYPE)
        monitor_label = utils.window(TMDB_MONITOR_LABEL)
        monitor_dbtype = utils.window(TMDB_MONITOR_DBTYPE)
        if (
            tmdb_id
            and tmdb_type in ("movie", "tv")
            and monitor_label == expected_label
            and monitor_dbtype == expected_dbtype
        ):
            if utils.window(DETAIL_MONITOR_REVISION) != monitor_revision:
                return False
            identity = plex_discover.tmdb_watchlist_identity(tmdb_id, tmdb_type)
            if identity is None:
                return False
            # The skin only exposes this fallback once all three properties
            # agree, so an enabled action always has its projection identity.
            utils.window(DETAIL_IDENTITY, value=identity)
            utils.window(DETAIL_TMDB_ID, value=tmdb_id)
            utils.window(DETAIL_TMDB_TYPE, value=tmdb_type)
            if utils.window(DETAIL_MONITOR_REVISION) != monitor_revision:
                return False
            return status_tmdb(
                {
                    "tmdb_id": tmdb_id,
                    "tmdb_type": tmdb_type,
                    "watchlist_identity": identity,
                }
            )
        if attempt + 1 < WATCHLIST_MONITOR_ATTEMPTS:
            xbmc.sleep(WATCHLIST_MONITOR_SLEEP_MS)

    LOG.debug("TMDb Helper did not produce a current identity for Watchlist status")
    return False
