#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Native Plex Watchlist operations shared by PKC entry points and service."""

from logging import getLogger
from contextlib import contextmanager
import threading
from time import time
from uuid import uuid4

import xbmc

from . import app, clientinfo, discover_cache, downloadutils, plex_discover, utils

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
DISCOVER_HINT_PROPERTY = "PlexWatchlistHint"

DETAIL_IDENTITY = "PKC.Watchlist.Detail.Identity"
DETAIL_STATE = "PKC.Watchlist.Detail.State"
DETAIL_PREVIOUS_STATE = "PKC.Watchlist.Detail.PreviousState"
DETAIL_PENDING = "PKC.Watchlist.Detail.Pending"
DETAIL_REQUEST_ID = "PKC.Watchlist.Detail.RequestId"
DETAIL_REVISION = "PKC.Watchlist.Detail.Revision"
DETAIL_STATUS_REVISION = "PKC.Watchlist.Detail.StatusRevision"
DETAIL_ERROR = "PKC.Watchlist.Detail.Error"
DETAIL_OPTIMISTIC_STATE = "PKC.Watchlist.Detail.OptimisticState"
DETAIL_OPTIMISTIC_IDENTITY = "PKC.Watchlist.Detail.OptimisticIdentity"
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
SESSION = threading.local()


def _ensure_runtime():
    """Initialise only the lightweight connection state in a plugin process."""
    if getattr(app, "CONN", None) is None:
        app.init(entrypoint=True)


def _headers(accept_json=False, token=None):
    headers = {"X-Plex-Token": utils.window("plex_token") if token is None else token}
    if accept_json:
        headers["Accept"] = "application/json"
    return clientinfo.getXArgsDeviceInfo(headers, include_token=False)


def _session_credentials():
    """Return the request-bound token when a mutation is in progress."""
    credentials = getattr(SESSION, "credentials", None)
    if credentials is not None:
        return credentials
    token = utils.window("plex_token")
    return token, discover_cache.account_hash_for_token(token)


@contextmanager
def _bound_session(token, account_hash):
    """Keep all I/O for one mutation bound to the initiating Plex account."""
    previous = getattr(SESSION, "credentials", None)
    SESSION.credentials = (token, account_hash)
    try:
        yield
    finally:
        if previous is None:
            del SESSION.credentials
        else:
            SESSION.credentials = previous


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
        LOG.warning(
            "discover_tmdb_ratingkey: Plex metadata match returned invalid JSON"
        )
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
    rating_key = discover_cache.cached_tmdb_rating_key(identity)
    if rating_key is not None:
        _remember_rating_key(identity, rating_key)
        return rating_key
    rating_key = discover_tmdb_ratingkey(
        params.get("tmdb_id"), params.get("tmdb_type") or params.get("plex_type")
    )
    if rating_key is not None:
        _remember_rating_key(identity, rating_key)
        discover_cache.record_tmdb_rating_key(identity, rating_key)
    return rating_key


def state(rating_key):
    """Read exact Watchlist membership from Plex's canonical userState API."""
    rating_key = plex_discover.normalize_rating_key(rating_key)
    if rating_key is None:
        LOG.warning("watchlist state requested with an invalid rating key")
        return None
    token, account_hash = _session_credentials()
    if (
        not token
        or account_hash is None
        or not discover_cache.account_matches(account_hash)
    ):
        return None
    _ensure_runtime()
    response = downloadutils.DownloadUtils().downloadUrl(
        WATCHLIST_USER_STATE_URL % rating_key,
        authenticate=False,
        headerOptions=_headers(accept_json=True, token=token),
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
    if not discover_cache.account_matches(account_hash):
        return None
    return plex_discover.watchlist_state_from_payload(payload, rating_key)


def _action(api_type, rating_key):
    token, account_hash = _session_credentials()
    if (
        not token
        or account_hash is None
        or not discover_cache.account_matches(account_hash)
    ):
        return False
    _ensure_runtime()
    response = downloadutils.DownloadUtils().downloadUrl(
        "https://discover.provider.plex.tv/actions/%s" % api_type,
        action_type="PUT",
        parameters={"ratingKey": rating_key},
        authenticate=False,
        headerOptions=_headers(token=token),
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
    return discover_cache.account_matches(account_hash)


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
    token = utils.window("plex_token")
    account_hash = discover_cache.account_hash_for_token(token)
    if (
        rating_key is None
        or desired_state is None
        or not token
        or account_hash is None
        or not discover_cache.account_matches(account_hash)
    ):
        return False, None

    with _bound_session(token, account_hash):
        with _lock(rating_key):
            current_state = state(rating_key)
            if not discover_cache.account_matches(account_hash):
                return False, None
            if current_state is desired_state:
                discover_cache.record_watchlist_state(
                    rating_key, current_state, account_hash
                )
                return True, current_state

            action_acknowledged = _action(api_type, rating_key)
            observed_state = None
            for attempt in range(WATCHLIST_VERIFY_ATTEMPTS):
                observed_state = state(rating_key)
                if not discover_cache.account_matches(account_hash):
                    return False, None
                if observed_state is desired_state:
                    discover_cache.record_watchlist_state(
                        rating_key, observed_state, account_hash
                    )
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


def _item_mutation_request_property(identity):
    return "%s%s.MutationRequest" % (ITEM_STATE_PREFIX, identity)


def _item_mutation_desired_property(identity):
    return "%s%s.MutationDesired" % (ITEM_STATE_PREFIX, identity)


def _item_mutation_previous_state_property(identity):
    return "%s%s.MutationPreviousState" % (ITEM_STATE_PREFIX, identity)


def _item_mutation_completed_property(identity):
    return "%s%s.MutationCompleted" % (ITEM_STATE_PREFIX, identity)


def _item_mutation_marker(identity):
    """Return the per-item mutation generation visible to status readers."""
    return (
        utils.window(_item_mutation_request_property(identity)),
        utils.window(_item_mutation_completed_property(identity)),
    )


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
    return plex_discover.normalize_rating_key(
        utils.window(_item_rating_key_property(identity))
    )


def _identity_for_key(params):
    rating_key = plex_discover.normalize_rating_key(params.get("rating_key"))
    return "plex.%s" % rating_key if rating_key else None


def supports_key_watchlist(params):
    """Plex accepts Watchlist mutations only for movie and show metadata."""
    return plex_discover.normalize_tmdb_type(params.get("plex_type")) is not None


def provider_state_hint(value):
    """Normalize Plex Discover's non-authoritative ``userState`` hint."""
    if value is None:
        return None
    return {
        "1": "present",
        "true": "present",
        "0": "absent",
        "false": "absent",
    }.get(str(value).strip().lower())


def seed_key_status_hint(params):
    """Seed an unknown direct-item state without displacing a live mutation."""
    identity = params.get("watchlist_identity") or _identity_for_key(params)
    hint = provider_state_hint(params.get("watchlist_hint"))
    if (
        identity is None
        or hint is None
        or not supports_key_watchlist(params)
        or _current_mutation_intent(identity) is not None
        or utils.window(_item_state_property(identity)) in ("present", "absent")
    ):
        return False
    # Do not allow a stale provider listing to replace the foreground's
    # optimistic projection while a mutation is being published.
    if utils.window(DETAIL_IDENTITY) == identity and (
        utils.window(DETAIL_PENDING) or _optimistic_projection_active(identity)
    ):
        return False
    _remember_state(identity, hint)
    return True


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


def _batch_previous_state(identity):
    previous_state = utils.window(_item_mutation_previous_state_property(identity))
    if previous_state in ("present", "absent"):
        return previous_state
    previous_state = _cached_state(identity)
    return previous_state if previous_state is not None else "absent"


def _current_mutation_intent(identity):
    request_id = utils.window(_item_mutation_request_property(identity))
    desired = utils.window(_item_mutation_desired_property(identity))
    if (
        not request_id
        or _api_type_for_desired(desired) is None
        or utils.window(_item_mutation_completed_property(identity)) == request_id
    ):
        return None
    return request_id, desired, _batch_previous_state(identity)


def _intent_lock(identity):
    return _lock("watchlist-intent:%s" % identity)


def _begin(identity, desired):
    if utils.window(DETAIL_IDENTITY) != identity:
        return None, None
    if _current_mutation_intent(identity) is not None:
        previous_state = _batch_previous_state(identity)
    else:
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
    # Publish the payload before its generation. A worker that wakes between
    # window-property calls can then observe either the completed old request
    # or a complete new intent, never a new request paired with old desired
    # state.
    utils.window(_item_mutation_desired_property(identity), value=desired)
    utils.window(_item_mutation_previous_state_property(identity), value=previous_state)
    utils.window(_item_mutation_request_property(identity), value=request_id)
    # A completion marker carries its generation, so a stale worker cannot
    # clear a foreground intent that is written while it terminally projects.
    utils.window(_item_mutation_completed_property(identity), clear=True)
    utils.window(DETAIL_ERROR, clear=True)
    utils.window(DETAIL_STATE, value=desired)
    return request_id, previous_state


def _state_name(value):
    if value is True:
        return "present"
    if value is False:
        return "absent"
    return None


def _mutation_pending(identity):
    return bool(
        identity
        and utils.window(DETAIL_IDENTITY) == identity
        and utils.window(DETAIL_PENDING)
    )


def _optimistic_projection_active(identity=None):
    if utils.window(DETAIL_OPTIMISTIC_STATE) not in ("present", "absent"):
        return False
    optimistic_identity = utils.window(DETAIL_OPTIMISTIC_IDENTITY)
    return (
        identity is None or not optimistic_identity or optimistic_identity == identity
    )


def _clear_optimistic_projection(identity=None):
    optimistic_identity = utils.window(DETAIL_OPTIMISTIC_IDENTITY)
    if identity is not None and optimistic_identity and optimistic_identity != identity:
        return False
    utils.window(DETAIL_OPTIMISTIC_STATE, clear=True)
    utils.window(DETAIL_OPTIMISTIC_IDENTITY, clear=True)
    return True


def _clear_current_optimistic_projection():
    """Drop a local projection only when it belongs to the active detail."""
    identity = utils.window(DETAIL_IDENTITY)
    if identity:
        _clear_optimistic_projection(identity)


def _worker_request_is_current(identity, request_id):
    return bool(
        identity
        and request_id
        and utils.window(_item_mutation_request_property(identity)) == request_id
        and utils.window(_item_mutation_completed_property(identity)) != request_id
    )


def _clear_worker_request(identity, request_id):
    if _worker_request_is_current(identity, request_id):
        # Do not clear individual intent properties here. Foreground plugin
        # processes can write a newer request between property calls. A
        # generation marker lets readers distinguish terminal A from active B
        # without ever erasing B's request, desired state, or rollback base.
        utils.window(_item_mutation_completed_property(identity), value=request_id)
        return True
    return False


def _detail_request_is_current(identity, request_id):
    return bool(
        identity
        and request_id
        and utils.window(DETAIL_IDENTITY) == identity
        and utils.window(DETAIL_PENDING) == request_id
        and utils.window(DETAIL_REQUEST_ID) == request_id
        and utils.window(DETAIL_REVISION) == request_id
    )


def _restore_detail_intent(identity):
    """Restore a still-pending item mutation when its detail dialog reopens."""
    intent = _current_mutation_intent(identity)
    if intent is None or utils.window(DETAIL_IDENTITY) != identity:
        return False
    request_id, desired, previous_state = intent
    # Write Pending first so a concurrent status probe can only reject itself.
    utils.window(DETAIL_PENDING, value=request_id)
    utils.window(DETAIL_PREVIOUS_STATE, value=previous_state)
    utils.window(DETAIL_REQUEST_ID, value=request_id)
    utils.window(DETAIL_REVISION, value=request_id)
    utils.window(DETAIL_STATE, value=desired)
    utils.window(DETAIL_ERROR, clear=True)
    return True


def _project_mutation(identity, request_id, observed_state):
    state_name = _state_name(observed_state)
    if identity is None or state_name is None:
        return False
    # Publish the authoritative result before terminally marking this intent.
    # A new foreground tap that races the terminal write then uses this as its
    # rollback base instead of the old optimistic detail state.
    _remember_state(identity, state_name)
    if not _clear_worker_request(identity, request_id):
        return False
    if not _detail_request_is_current(identity, request_id):
        return True
    utils.window(DETAIL_STATE, value=state_name)
    _clear_optimistic_projection(identity)
    utils.window(DETAIL_ERROR, clear=True)
    utils.window(DETAIL_REQUEST_ID, clear=True)
    # Clear last: this is the skin's double-click guard.
    utils.window(DETAIL_PENDING, clear=True)
    return True


def _project_failure(identity, request_id, observed_state, previous_state):
    confirmed_name = _state_name(observed_state)
    fallback_state = (
        previous_state if previous_state in ("present", "absent") else "absent"
    )
    # Preserve the best confirmed state before a racing next intent reads it.
    _remember_state(identity, confirmed_name or fallback_state)
    if not _clear_worker_request(identity, request_id):
        return False
    if not _detail_request_is_current(identity, request_id):
        return True
    utils.window(DETAIL_STATE, value=confirmed_name or fallback_state)
    _clear_optimistic_projection(identity)
    utils.window(DETAIL_ERROR, value="verification-failed")
    utils.window(DETAIL_REQUEST_ID, clear=True)
    # Clear last: this is the skin's double-click guard.
    utils.window(DETAIL_PENDING, clear=True)
    return True


def _api_type_for_desired(desired):
    return {"present": "addToWatchlist", "absent": "removeFromWatchlist"}.get(desired)


def _begin_detail_request(params, desired, identity):
    if _api_type_for_desired(desired) is None or identity is None:
        if identity is not None:
            _clear_optimistic_projection(identity)
        return None
    request_id, previous_state = _begin(identity, desired)
    if request_id is None:
        _clear_optimistic_projection(identity)
        return None
    request = dict(params)
    request.update(
        {
            "desired": desired,
            "watchlist_identity": identity,
            "request_id": request_id,
            "previous_state": previous_state,
        }
    )
    return request


def begin_key(params, desired):
    """Project a detail mutation immediately and return its worker payload."""
    rating_key = plex_discover.normalize_rating_key(params.get("rating_key"))
    identity = _identity_for_key(params)
    if rating_key is None or identity is None:
        _clear_current_optimistic_projection()
        notify_error()
        return None
    if not supports_key_watchlist(params):
        _clear_current_optimistic_projection()
        return None
    return _begin_detail_request(
        {
            "rating_key": rating_key,
            "plex_type": params.get("plex_type"),
        },
        desired,
        identity,
    )


def begin_tmdb(params, desired):
    """Project an exact-TMDb detail mutation without performing network I/O."""
    identity = _identity_for_tmdb(params)
    if identity is None:
        _clear_current_optimistic_projection()
        notify_error("Plex could not uniquely match this TMDb item for Watchlist.")
        return None
    tmdb_type = params.get("tmdb_type") or params.get("plex_type")
    return _begin_detail_request(
        {"tmdb_id": params.get("tmdb_id"), "tmdb_type": tmdb_type},
        desired,
        identity,
    )


def _detail_worker_kick_is_valid(params, identity):
    """Validate a worker wake-up without requiring its now-stale generation."""
    return bool(
        identity
        and params.get("watchlist_identity") == identity
        and params.get("request_id")
        and _api_type_for_desired(params.get("desired")) is not None
    )


def _reconcile_detail_intent(identity, resolve_rating_key, unresolved_message=None):
    """Drain the latest requested state for one detail item.

    Foreground plugin invocations only publish an intent and enqueue a wake-up.
    Several wake-ups may arrive, but this serialized reconciler always performs
    only the newest intent it can observe. A newer tap that lands during I/O
    becomes the next iteration instead of being rejected or painted stale.
    """
    with _intent_lock(identity):
        while True:
            intent = _current_mutation_intent(identity)
            if intent is None:
                # Another queued wake-up already completed the latest intent.
                return True
            request_id, desired, previous_state = intent
            rating_key = resolve_rating_key()

            # Exact TMDb resolution can take long enough for a newer foreground
            # intent to arrive. Do not issue an obsolete action in that case.
            if not _worker_request_is_current(identity, request_id):
                LOG.debug("Watchlist intent advanced while resolving %s", identity)
                continue
            if rating_key is None:
                if _project_failure(identity, request_id, None, previous_state):
                    if unresolved_message is None:
                        notify_error()
                    else:
                        notify_error(unresolved_message)
                    return False
                # A newer intent arrived while the failure was being projected.
                continue

            success, observed_state = change(_api_type_for_desired(desired), rating_key)

            # Only the still-current generation may paint UI, clear the durable
            # intent, or raise an error. A stale failure is intentionally silent.
            if not _worker_request_is_current(identity, request_id):
                LOG.debug("Watchlist intent advanced during mutation for %s", identity)
                continue
            if success and _state_name(observed_state) is not None:
                if _project_mutation(identity, request_id, observed_state):
                    return True
                # A foreground tap won the race with terminal projection.
                continue
            if success:
                LOG.warning(
                    "Plex Watchlist %s reported success without a valid state for %s",
                    desired,
                    identity,
                )
            if _project_failure(identity, request_id, observed_state, previous_state):
                notify_error()
                return False
            # A foreground tap won the race with failure projection.


def complete_key(params):
    """Verify and finish a previously projected rating-key detail mutation."""
    identity = _identity_for_key(params)
    if not supports_key_watchlist(params) or not _detail_worker_kick_is_valid(
        params, identity
    ):
        return False
    rating_key = plex_discover.normalize_rating_key(params.get("rating_key"))
    if rating_key is None:
        return False
    return _reconcile_detail_intent(identity, lambda: rating_key)


def complete_tmdb(params):
    """Resolve, verify, and finish a previously projected TMDb detail mutation."""
    identity = _identity_for_tmdb(params)
    if not _detail_worker_kick_is_valid(params, identity):
        return False
    return _reconcile_detail_intent(
        identity,
        lambda: _rating_key_for_tmdb(params, identity),
        "Plex could not uniquely match this TMDb item for Watchlist.",
    )


def _legacy_change(api_type, rating_key):
    """Keep non-detail callers on the prior verified synchronous contract."""
    success, _ = change(api_type, rating_key)
    if not success:
        notify_error()
    return success


def set_key(params, desired):
    """Synchronously update a direct item for legacy callers."""
    api_type = _api_type_for_desired(desired)
    rating_key = plex_discover.normalize_rating_key(params.get("rating_key"))
    identity = _identity_for_key(params)
    if api_type is None or not supports_key_watchlist(params):
        return False
    if rating_key is None or identity is None:
        notify_error()
        return False
    if utils.window(DETAIL_IDENTITY) != identity:
        return _legacy_change(api_type, rating_key)
    if _mutation_pending(identity):
        return False
    request = begin_key(params, desired)
    return complete_key(request) if request is not None else False


def set_tmdb(params, desired):
    """Synchronously update a TMDb item for legacy callers."""
    api_type = _api_type_for_desired(desired)
    if api_type is None:
        return False
    identity = _identity_for_tmdb(params)
    if identity is None:
        notify_error("Plex could not uniquely match this TMDb item for Watchlist.")
        return False
    if utils.window(DETAIL_IDENTITY) != identity:
        rating_key = _rating_key_for_tmdb(params, identity)
        if rating_key is None:
            notify_error("Plex could not uniquely match this TMDb item for Watchlist.")
            return False
        return _legacy_change(api_type, rating_key)
    if _mutation_pending(identity):
        return False
    request = begin_tmdb(params, desired)
    return complete_tmdb(request) if request is not None else False


def _capture_status(identity):
    # Capture mutation state before checking Pending. If an action begins
    # between these reads, its revision differs and this stale probe rejects.
    mutation_revision = utils.window(DETAIL_REVISION)
    if utils.window(DETAIL_IDENTITY) != identity:
        return None
    if _current_mutation_intent(identity) is not None:
        # The item-level intent survives a dialog close. Rehydrate it before a
        # new detail's status probe can paint an older server observation.
        _restore_detail_intent(identity)
        return None
    if (
        utils.window(DETAIL_PENDING)
        or _optimistic_projection_active(identity)
        or utils.window(DETAIL_REVISION) != mutation_revision
        or _current_mutation_intent(identity) is not None
    ):
        return None
    # Status reads get an independent token and snapshot the current mutation
    # revision. They must never overwrite an in-flight or completed mutation.
    status_revision = uuid4().hex
    utils.window(DETAIL_STATUS_REVISION, value=status_revision)
    return status_revision, mutation_revision


def _project_status(identity, status_revision, mutation_revision, observed_state):
    state_name = _state_name(observed_state)
    if _current_mutation_intent(identity) is not None:
        _restore_detail_intent(identity)
        return False
    if (
        state_name is None
        or utils.window(DETAIL_IDENTITY) != identity
        or utils.window(DETAIL_STATUS_REVISION) != status_revision
        or utils.window(DETAIL_REVISION) != mutation_revision
        or utils.window(DETAIL_PENDING)
        or _optimistic_projection_active(identity)
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


def _remember_status_if_current(identity, mutation_marker, observed_state):
    """Warm the item cache after I/O without resurrecting a stale mutation."""
    state_name = _state_name(observed_state)
    if state_name is None or _item_mutation_marker(identity) != mutation_marker:
        return False
    # `_begin()` writes Pending before its item-level mutation marker.  This
    # prevents a status response that races that short publication window from
    # caching the pre-mutation server state.
    if utils.window(DETAIL_IDENTITY) == identity and utils.window(DETAIL_PENDING):
        return False
    _remember_state(identity, state_name)
    return True


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
    seed_key_status_hint(params)
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
    if identity is None or rating_key is None or not supports_key_watchlist(params):
        return False
    account_hash = discover_cache.active_account_hash()
    if account_hash is None:
        return False
    seed_key_status_hint(params)
    revision = _capture_status(identity)
    if revision is None:
        return False
    mutation_marker = _item_mutation_marker(identity)
    _project_cached_status(identity, revision[0], revision[1])
    observed_state = state(rating_key)
    if not discover_cache.account_matches(account_hash):
        return False
    accepted = _remember_status_if_current(identity, mutation_marker, observed_state)
    if accepted:
        discover_cache.record_watchlist_state(rating_key, observed_state, account_hash)
    return _project_status(identity, revision[0], revision[1], observed_state)


def status_tmdb(params):
    """Silently project an authoritative state for the active TMDb detail."""
    identity = params.get("watchlist_identity") or _identity_for_tmdb(params)
    if identity is None:
        return False
    account_hash = discover_cache.active_account_hash()
    if account_hash is None:
        return False
    revision = _capture_status(identity)
    if revision is None:
        return False
    mutation_marker = _item_mutation_marker(identity)
    _project_cached_status(identity, revision[0], revision[1])
    rating_key = _rating_key_for_tmdb(params, identity)
    if rating_key is None:
        return False
    observed_state = state(rating_key)
    if not discover_cache.account_matches(account_hash):
        return False
    accepted = _remember_status_if_current(identity, mutation_marker, observed_state)
    if accepted:
        discover_cache.record_watchlist_state(rating_key, observed_state, account_hash)
    return _project_status(identity, revision[0], revision[1], observed_state)


# --- title+year search resolver (bypasses TMDb Helper dependency) ------------

DETAIL_CONTEXT_YEAR = "PKC.Watchlist.Detail.Context.Year"
DISCOVER_SEARCH_URL = "https://discover.provider.plex.tv/library/search"


def _exact_search_match(payload, expected_title, expected_year, media_type):
    """3-tier match on Discover search results — never fuzzy."""
    expected_type = "movie" if media_type == "movie" else "show"
    title_lower = (expected_title or "").strip().lower()
    if not title_lower:
        return None
    year_int = None
    if expected_year and str(expected_year).strip().isdigit():
        year_int = int(str(expected_year).strip())
    candidates = []
    container = payload.get("MediaContainer") if isinstance(payload, dict) else None
    if not isinstance(container, dict):
        return None
    for result_group in plex_discover._as_list(container.get("SearchResults")):
        if not isinstance(result_group, dict):
            continue
        for result in plex_discover._as_list(result_group.get("SearchResult")):
            if not isinstance(result, dict):
                continue
            metadata = result.get("Metadata")
            if not isinstance(metadata, dict):
                continue
            if metadata.get("type") != expected_type:
                continue
            rk = metadata.get("ratingKey")
            if not rk:
                continue
            match_title = (metadata.get("title") or "").strip().lower()
            candidates.append(
                {
                    "rating_key": rk,
                    "title": match_title,
                    "year": metadata.get("year"),
                    "score": float(result.get("score", 0) or 0),
                }
            )
    if not candidates:
        return None
    # Tier 1: exact title + exact year
    if year_int is not None:
        for c in candidates:
            if c["title"] == title_lower and c["year"] == year_int:
                return c["rating_key"]
    # Tier 2: exact title, highest score
    exact_title = [c for c in candidates if c["title"] == title_lower]
    if exact_title:
        return max(exact_title, key=lambda c: c["score"])["rating_key"]
    # Tier 3: no exact title match — refuse to guess
    return None


def discover_search_ratingkey(title, year, plex_type):
    """Resolve a title+year to Plex's canonical ratingKey via Discover search."""
    media_type = plex_discover.normalize_tmdb_type(plex_type)
    if media_type is None or not title:
        return None
    search_types = "movies" if media_type == "movie" else "tv"
    _ensure_runtime()
    response = downloadutils.DownloadUtils().downloadUrl(
        DISCOVER_SEARCH_URL,
        parameters={
            "query": title,
            "searchTypes": search_types,
            "searchProviders": "discover",
            "includeMetadata": 1,
            "limit": 5,
        },
        authenticate=False,
        headerOptions=_headers(accept_json=True),
        return_response=True,
        timeout=WATCHLIST_HTTP_TIMEOUT,
    )
    if response is None or not getattr(response, "ok", False):
        LOG.warning(
            "discover_search_ratingkey: search returned HTTP %s",
            getattr(response, "status_code", "unknown"),
        )
        return None
    try:
        payload = response.json()
    except (TypeError, ValueError):
        LOG.warning("discover_search_ratingkey: invalid JSON response")
        return None
    return _exact_search_match(payload, title, year, media_type)


def _identity_for_search(params):
    title = params.get("title")
    year = params.get("year")
    plex_type = params.get("plex_type") or params.get("tmdb_type")
    media_type = plex_discover.normalize_tmdb_type(plex_type)
    if not title or media_type is None:
        return None
    return "search.%s.%s.%s" % (media_type, title.lower(), year or "")


def bootstrap_status_search(params):
    """Restore a cached state for a title+year search detail item."""
    title = params.get("title") or utils.window(DETAIL_CONTEXT_LABEL)
    year = params.get("year") or utils.window(DETAIL_CONTEXT_YEAR)
    plex_type = params.get("plex_type") or params.get("tmdb_type")
    if not title:
        return False
    enriched = dict(params)
    enriched["title"] = title
    enriched["year"] = year
    enriched["plex_type"] = plex_type
    identity = _identity_for_search(enriched)
    if identity is None:
        return False
    # The search tier runs when no higher tier set Identity from a known ID.
    # Set it here so _capture_status and the service-side resolver agree.
    utils.window(DETAIL_IDENTITY, value=identity)
    return _bootstrap_cached_status(identity)


def status_search(params):
    """Resolve watchlist state by title+year, bypassing TMDb Helper dependency."""
    title = params.get("title") or utils.window(DETAIL_CONTEXT_LABEL)
    year = params.get("year") or utils.window(DETAIL_CONTEXT_YEAR)
    plex_type = params.get("plex_type") or params.get("tmdb_type")
    media_type = plex_discover.normalize_tmdb_type(plex_type)
    if not title or media_type is None:
        return False
    identity = _identity_for_search(
        {"title": title, "year": year, "plex_type": plex_type}
    )
    if identity is None:
        return False
    account_hash = discover_cache.active_account_hash()
    if account_hash is None:
        return False
    revision = _capture_status(identity)
    if revision is None:
        return False
    mutation_marker = _item_mutation_marker(identity)
    _project_cached_status(identity, revision[0], revision[1])
    rating_key = discover_cache.cached_tmdb_rating_key(identity)
    if rating_key is None:
        rating_key = discover_search_ratingkey(title, year, plex_type)
    if rating_key is None:
        return False
    observed_state = state(rating_key)
    if not discover_cache.account_matches(account_hash):
        return False
    accepted = _remember_status_if_current(identity, mutation_marker, observed_state)
    if accepted:
        discover_cache.record_watchlist_state(rating_key, observed_state, account_hash)
        discover_cache.record_tmdb_rating_key(identity, rating_key, account_hash)
    return _project_status(identity, revision[0], revision[1], observed_state)


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
        if utils.window(
            DETAIL_MONITOR_REVISION
        ) != monitor_revision or _monitor_context() != (
            expected_label,
            expected_dbtype,
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
