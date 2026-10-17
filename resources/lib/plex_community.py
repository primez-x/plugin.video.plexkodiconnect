#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bounded, account-scoped reads for Plex Community reviews and Lists."""

from hashlib import sha256
import json
import re
import xml.etree.ElementTree as etree

from . import clientinfo, discover_cache, utils
from .downloadutils import DownloadUtils as DU


COMMUNITY_URL = "https://community.plex.tv/api"
ACCOUNT_URL = "https://plex.tv/api/v2/user"
PAGE_SIZE = 24
MAX_MEMBERSHIP_PAGES = 20
MAX_RESPONSE_BYTES = 512 * 1024
HTTP_TIMEOUT = (3, 5)
SELECTION_SETTING = "plex_favourite_lists"
_CACHE_TAG = "PlexCommunity"

_LISTS_QUERY = (
    "query PKCLists($user: UserInput!, $first: PaginationInt!, $after: String) { "
    "userV2(user: $user) { __typename ... on User { "
    "customLists(first: $first, after: $after) { nodes { id name slug itemCount } "
    "pageInfo { hasNextPage endCursor } } } } }"
)
_LIST_QUERY = (
    "query PKCList($slug: String!, $username: String!, $first: PaginationInt!, "
    "$after: String) { customListBySlug(slug: $slug, username: $username) { "
    "id name slug itemCount metadataItems(first: $first, after: $after) { "
    "nodes { id type title } pageInfo { hasNextPage endCursor } } } }"
)
_REVIEW_FIELDS = (
    "id date message hasSpoilers reviewRating: rating "
    "metadataItem { id type } userV2 { displayName avatar } "
)
_REVIEWS_QUERY = (
    "query PKCReviews($metadataID: ID!, $type: ReviewsListType!, "
    "$first: PaginationInt, $after: String) { "
    "metadataReviewsV2(metadata: {id: $metadataID}, type: $type, "
    "first: $first, after: $after) { nodes { __typename "
    "... on ActivityReview { " + _REVIEW_FIELDS + "} "
    "... on ActivityWatchReview { " + _REVIEW_FIELDS + "} "
    "} pageInfo { endCursor hasNextPage } } }"
)


def _text(value, limit, empty=False):
    return (
        isinstance(value, str)
        and len(value) <= limit
        and (empty or bool(value.strip()))
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def _opaque_id(value):
    return _text(value, 256) and not any(char.isspace() for char in value)


def _cloud_id(value):
    # Local PMS numeric keys cannot identify cloud metadata.
    return (
        value.lower()
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{24}", value)
        else None
    )


def _slug(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value)


def _cursor(value):
    return value is None or (
        isinstance(value, str)
        and 0 < len(value) <= 2048
        and all(33 <= ord(char) <= 126 for char in value)
    )


def _active(account_hash):
    return bool(account_hash) and discover_cache.account_matches(account_hash)


def _reject_constant(value):
    raise ValueError("Non-standard JSON constant")


def _http(account_hash, query=None):
    """Use fixed endpoints and reject responses from a previous account."""
    token = utils.window("plex_token")
    if (
        not token
        or not _active(account_hash)
        or discover_cache.account_hash_for_token(token) != account_hash
    ):
        return None
    try:
        headers = clientinfo.getXArgsDeviceInfo(
            {"X-Plex-Token": token, "Accept": "application/json",
             "Content-Type": "application/json"},
            include_token=False,
        )
        response = DU().downloadUrl(
            ACCOUNT_URL if query is None else COMMUNITY_URL,
            action_type="GET" if query is None else "POST",
            postBody=None if query is None else json.dumps(query),
            authenticate=False,
            headerOptions=headers,
            return_response=True,
            allow_redirects=False,
            timeout=HTTP_TIMEOUT,
        )
        if not _active(account_hash) or getattr(response, "status_code", None) != 200:
            return None
        content = response.content
        if not isinstance(content, (bytes, str)):
            return None
        if isinstance(content, str):
            content = content.encode("utf-8")
        if len(content) > MAX_RESPONSE_BYTES:
            return None
        data = json.loads(content, parse_constant=_reject_constant)
        return data if isinstance(data, dict) and _active(account_hash) else None
    except Exception:
        # Provider bodies can contain review text and account secrets.
        return None


def _graphql(account_hash, operation, query, variables):
    body = _http(account_hash, {
        "operationName": operation, "query": query, "variables": variables})
    if body is None or ("errors" in body and body["errors"] != []):
        return None
    data = body.get("data")
    return data if isinstance(data, dict) else None


def _cache_key(operation, arguments):
    material = json.dumps([operation, arguments], sort_keys=True,
                          separators=(",", ":"), ensure_ascii=True)
    return "plex-community:" + sha256(material.encode("utf-8")).hexdigest()


def _read_cached(key, kind, account_hash, normalize):
    if not _active(account_hash):
        return None
    freshness, xmls = discover_cache.read_xml(key, kind)
    if freshness != discover_cache.CACHE_FRESH or not _active(account_hash):
        return None
    try:
        if len(xmls) != 1 or xmls[0].tag != _CACHE_TAG:
            return None
        text = xmls[0].text
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
            return None
        result = normalize(json.loads(text, parse_constant=_reject_constant))
        return result if _active(account_hash) else None
    except (TypeError, ValueError, RecursionError):
        return None


def _write_cached(key, kind, account_hash, result):
    if not _active(account_hash):
        return
    root = etree.Element(_CACHE_TAG)
    root.text = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
    discover_cache.put_xml(key, [root], kind, account_hash=account_hash)


def _cached(operation, arguments, kind, account_hash, load, normalize):
    if not _active(account_hash):
        return None
    key = _cache_key(operation, arguments)
    cached = _read_cached(key, kind, account_hash, normalize)
    if cached is not None:
        return cached
    result = normalize(load())
    if result is None or not _active(account_hash):
        return None
    _write_cached(key, kind, account_hash, result)
    return result if _active(account_hash) else None


def _normalize_account(value):
    if not isinstance(value, dict) or not _text(value.get("username"), 128):
        return None
    identifier = value.get("id")
    if not _opaque_id(identifier):
        return None
    preference = value.get("review_visibility")
    if type(preference) is not int or preference not in (0, 1, 2, 3):
        preference = None
    return {"id": identifier, "username": value["username"],
            "review_visibility": preference}


def account():
    """Return only the current account identity and review preference."""
    account_hash = discover_cache.active_account_hash()

    def load():
        raw = _http(account_hash)
        if raw is None:
            return None
        profile = raw.get("profile")
        return {"id": raw.get("uuid"), "username": raw.get("username"),
                "review_visibility": profile.get("mediaReviewsVisibility")
                if isinstance(profile, dict) else None}

    return _cached("account", {}, "community_account", account_hash,
                   load, _normalize_account)


def review_preference():
    """Return the verified account preference enum, or unknown."""
    current = account()
    return current["review_visibility"] if current is not None else None


def _connection(value, after, normalize_node):
    if not isinstance(value, dict):
        return None
    nodes, page = value.get("nodes"), value.get("pageInfo")
    if not isinstance(nodes, list) or len(nodes) > PAGE_SIZE or not isinstance(page, dict):
        return None
    has_next, end = page.get("hasNextPage"), page.get("endCursor")
    if type(has_next) is not bool or "endCursor" not in page or not _cursor(end):
        return None
    if (end is not None and end == after) or (has_next and (end is None or not nodes)):
        return None
    normalized = []
    for node in nodes:
        node = normalize_node(node)
        if node is None:
            return None
        normalized.append(node)
    return {"nodes": normalized, "pageInfo": {"hasNextPage": has_next, "endCursor": end}}


def _review(value, rating_key):
    if not isinstance(value, dict) or not _opaque_id(value.get("id")):
        return None
    metadata, user = value.get("metadataItem"), value.get("userV2")
    if (
        not isinstance(metadata, dict)
        or _cloud_id(metadata.get("id")) != rating_key
        or not _text(metadata.get("type"), 64)
        or not isinstance(user, dict)
        or type(value.get("hasSpoilers")) is not bool
    ):
        return None
    message, date = value.get("message"), value.get("date")
    if (
        not isinstance(message, str) or len(message) > 64 * 1024
        or not _text(date, 128)
    ):
        return None
    author = {}
    for field, limit in (("displayName", 256), ("avatar", 4096)):
        text = user.get(field)
        if text is not None and not _text(text, limit, empty=True):
            return None
        author[field] = text
    rating = value.get("reviewRating")
    if rating is not None and (
        type(rating) not in (int, float) or not 0 <= rating <= 10
    ):
        return None
    return {"id": value["id"], "message": message,
            "hasSpoilers": value["hasSpoilers"], "reviewRating": rating, "date": date,
            "metadataItem": {"id": rating_key, "type": metadata["type"]}, "userV2": author}


def reviews(rating_key, after=None):
    """Return a RECENT review page with exact cloud metadata identity."""
    rating_key = _cloud_id(rating_key)
    if rating_key is None or not _cursor(after):
        return None
    account_hash = discover_cache.active_account_hash()
    arguments = {"metadataID": rating_key, "type": "RECENT", "first": PAGE_SIZE,
                 "after": after}

    def load():
        data = _graphql(account_hash, "PKCReviews", _REVIEWS_QUERY, arguments)
        return data.get("metadataReviewsV2") if data is not None else None

    return _cached("reviews", arguments, "community_reviews", account_hash, load,
                   lambda value: _connection(value, after,
                                              lambda node: _review(node, rating_key)))


def _list(value):
    if not isinstance(value, dict):
        return None
    if (
        not _opaque_id(value.get("id")) or not _text(value.get("name"), 1024)
        or not _slug(value.get("slug")) or type(value.get("itemCount")) is not int
        or value["itemCount"] < 0
    ):
        return None
    return {field: value[field] for field in ("id", "name", "slug", "itemCount")}


def lists(after=None):
    """Return the current account's Lists with the account username."""
    if not _cursor(after):
        return None
    account_hash = discover_cache.active_account_hash()
    if not _active(account_hash):
        return None
    current = account()
    if current is None or not _active(account_hash):
        return None
    arguments = {"user": {"id": current["id"]}, "first": PAGE_SIZE, "after": after}

    def load():
        data = _graphql(account_hash, "PKCLists", _LISTS_QUERY, arguments)
        user = data.get("userV2") if data is not None else None
        return user.get("customLists") if isinstance(user, dict) else None

    def node(value):
        normalized = _list(value)
        if normalized is not None:
            normalized["username"] = current["username"]
        return normalized

    return _cached("lists", arguments, "community_lists", account_hash, load,
                   lambda value: _connection(value, after, node))


def _metadata_node(value):
    if not isinstance(value, dict):
        return None
    identifier = _cloud_id(value.get("id"))
    if (identifier is None or not _text(value.get("type"), 64)
            or not _text(value.get("title"), 4096, empty=True)):
        return None
    return {"id": identifier, "type": value["type"], "title": value["title"]}


def list_items(slug, username, after=None):
    """Return one ordered List page, including unsupported metadata types."""
    if not _slug(slug) or not _text(username, 128) or not _cursor(after):
        return None
    account_hash = discover_cache.active_account_hash()
    arguments = {"slug": slug, "username": username, "first": PAGE_SIZE, "after": after}

    def load():
        data = _graphql(account_hash, "PKCList", _LIST_QUERY, arguments)
        raw = data.get("customListBySlug") if data is not None else None
        if not isinstance(raw, dict) or not isinstance(raw.get("metadataItems"), dict):
            return None
        return dict(raw, nodes=raw["metadataItems"].get("nodes"),
                    pageInfo=raw["metadataItems"].get("pageInfo"))

    def normalize(value):
        result = _list(value)
        page = _connection(value, after, _metadata_node)
        if result is None or page is None or result["slug"] != slug:
            return None
        result.update(page)
        return result

    return _cached("list_items", arguments, "community_list_items", account_hash,
                   load, normalize)


def _selection(value):
    if not isinstance(value, dict):
        return None
    if (not _opaque_id(value.get("id")) or not _text(value.get("name"), 1024)
            or not _slug(value.get("slug")) or not _text(value.get("username"), 128)):
        return None
    return {field: value[field] for field in ("id", "name", "slug", "username")}


def _selections():
    try:
        raw = utils.settings(SELECTION_SETTING)
        if not raw:
            return {}
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
            return None
        value = json.loads(raw, parse_constant=_reject_constant)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError, RecursionError):
        return None


def selected_list():
    """Read only the active account's validated local selection."""
    account_hash = discover_cache.active_account_hash()
    if not _active(account_hash):
        return None
    selections = _selections()
    result = _selection(selections.get(account_hash)) if selections is not None else None
    return result if _active(account_hash) else None


def save_selected_list(selection_or_none):
    """Save or clear one account selection without changing other accounts."""
    account_hash = discover_cache.active_account_hash()
    if not _active(account_hash):
        return False
    selection = _selection(selection_or_none)
    if selection_or_none is not None and selection is None:
        return False
    selections = _selections()
    if selections is None:
        return False
    if selection is None:
        selections.pop(account_hash, None)
    else:
        selections[account_hash] = selection
    if not _active(account_hash):
        return False
    utils.settings(SELECTION_SETTING, json.dumps(selections, separators=(",", ":")))
    return _active(account_hash)


def favourite_membership(cached_only=False):
    """Return complete selected-List membership, or unknown on any gap."""
    account_hash = discover_cache.active_account_hash()
    selection = selected_list()
    if selection is None or not _active(account_hash):
        return None
    key = _cache_key("membership", selection)

    def normalize(value):
        if not isinstance(value, list) or len(value) > PAGE_SIZE * MAX_MEMBERSHIP_PAGES:
            return None
        keys = [_cloud_id(identifier) for identifier in value]
        return keys if None not in keys and len(set(keys)) == len(keys) else None

    cached = _read_cached(key, "community_membership", account_hash, normalize)
    if cached is not None:
        return set(cached) if selected_list() == selection and _active(account_hash) else None
    if cached_only:
        return None
    after, count = None, None
    cursors, members = set(), set()
    for _ in range(MAX_MEMBERSHIP_PAGES):
        page = list_items(selection["slug"], selection["username"], after)
        if page is None or not _active(account_hash) or selected_list() != selection:
            return None
        if page["id"] != selection["id"]:
            return None
        if count is None:
            count = page["itemCount"]
        if page["itemCount"] != count:
            return None
        for node in page["nodes"]:
            if node["id"] in members:
                return None
            members.add(node["id"])
        if len(members) > count:
            return None
        info = page["pageInfo"]
        if not info["hasNextPage"]:
            if len(members) != count:
                return None
            _write_cached(key, "community_membership", account_hash, sorted(members))
            return members if _active(account_hash) and selected_list() == selection else None
        after = info["endCursor"]
        if after in cursors:
            return None
        cursors.add(after)
    return None
