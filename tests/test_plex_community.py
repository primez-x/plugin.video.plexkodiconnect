"""Failure contracts for the Community boundary, with real durable cache IO."""

from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
KEY = "5d7768244de0ee001fcc7fed"
OTHER = "6244890dd4640db32655f36b"
ACCOUNT = {"uuid": "0123456789abcdef",
           "username": "owner", "profile": {"mediaReviewsVisibility": 1},
           "authToken": "must-not-persist", "services": [{"token": "secret"}]}
SELECTION = {"id": "list-one", "name": "Selected", "slug": "selected",
             "username": "owner"}


def connection(nodes=(), next_page=False, cursor=None):
    return {"nodes": list(nodes),
            "pageInfo": {"hasNextPage": next_page, "endCursor": cursor}}


def review(metadata=KEY):
    return {"id": "review-one", "date": "2026-10-17T00:00:00Z",
            "message": "A spoiler belongs behind the reveal action.",
            "hasSpoilers": True, "reviewRating": 8,
            "metadataItem": {"id": metadata, "type": "MOVIE"},
            "userV2": {"displayName": "Reviewer", "avatar": "https://plex.tv/a"}}


def list_page(nodes=(), next_page=False, cursor=None, count=0):
    return {"data": {"customListBySlug": {
        "id": "list-one", "name": "Selected", "slug": "selected",
        "itemCount": count, "metadataItems": connection(nodes, next_page, cursor)}}}


class CommunityTests(unittest.TestCase):
    def test_foreground_membership_miss_never_waits_for_provider_pages(self):
        self.client.save_selected_list(SELECTION, self.cache.active_account_hash())
        self.assertIsNone(self.client.favourite_membership(cached_only=True))
        self.assertEqual(self.calls, [])

    def test_background_membership_refresh_accepts_only_the_fixed_task_url(self):
        self.assertTrue(self.cache.enqueue_refresh(
            "https://community.plex.tv/api/favourites", "community_membership"))
        self.assertFalse(self.cache.enqueue_refresh(
            "https://community.plex.tv/api/favourites?query=other", "community_membership"))

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.profile = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.properties = {"plex_token": "account-one"}
        self.settings = {}
        self.calls = []
        self.responses = []
        self.clock = 100.0
        path = ROOT / "resources/lib/plex_community.py"
        self.assertTrue(path.exists(), "Shared Community provider boundary is absent")
        import resources.lib as package
        from resources.lib import plex_discover

        utils = types.ModuleType("resources.lib.utils")

        def window(key, value=None, clear=False):
            if clear:
                self.properties.pop(key, None)
            elif value is not None:
                self.properties[key] = value
            else:
                return self.properties.get(key, "")

        def settings(key, value=None):
            if value is None:
                return self.settings.get(key, "")
            self.settings[key] = value

        utils.window = window
        utils.settings = settings
        variables = types.ModuleType("resources.lib.variables")
        variables.ADDON_PROFILE = self.profile
        clientinfo = types.ModuleType("resources.lib.clientinfo")
        clientinfo.getXArgsDeviceInfo = lambda options, include_token: dict(options)
        downloadutils = types.ModuleType("resources.lib.downloadutils")
        downloadutils.DownloadUtils = lambda: types.SimpleNamespace(
            downloadUrl=self.download)
        dependencies = {"utils": utils, "variables": variables,
                        "plex_discover": plex_discover, "clientinfo": clientinfo,
                        "downloadutils": downloadutils}
        self.stack.enter_context(patch.dict(sys.modules, {
            "resources.lib." + name: module for name, module in dependencies.items()}))
        for name, module in dependencies.items():
            self.stack.enter_context(patch.object(package, name, module, create=True))
        self.cache = self.load("discover_cache")
        self.cache.time = lambda: self.clock
        self.stack.enter_context(patch.dict(sys.modules, {
            "resources.lib.discover_cache": self.cache}))
        self.stack.enter_context(patch.object(package, "discover_cache", self.cache,
                                             create=True))
        self.client = self.load("plex_community")

    @staticmethod
    def load(name):
        spec = importlib.util.spec_from_file_location(
            "resources.lib." + name, ROOT / "resources/lib" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def download(self, url, **kwargs):
        self.calls.append((url, kwargs))
        self.assertTrue(self.responses, "Unexpected network request")
        response = self.responses.pop(0)
        if callable(response):
            response = response()
        if isinstance(response, Exception):
            raise response
        return response

    def respond(self, body, status=200):
        content = json.dumps(body).encode("utf-8")
        self.responses.append(types.SimpleNamespace(status_code=status,
                                                    content=content))

    def respond_reviews(self, nodes=(), next_page=False, cursor=None):
        self.respond({"data": {"metadataReviewsV2":
                               connection(nodes, next_page, cursor)}})

    def test_account_strips_sensitive_payload_before_cache_and_preserves_preference(self):
        self.respond(ACCOUNT)
        expected = {"id": ACCOUNT["uuid"], "username": "owner", "review_visibility": 1}
        self.assertEqual(self.client.account(), expected)
        self.assertEqual(self.client.account(), expected)
        self.assertEqual(self.client.review_preference(), 1)
        self.assertEqual(len(self.calls), 1)
        records = list(Path(self.profile).rglob("*.json"))
        self.assertTrue(records)
        decoded = []
        for filename in records:
            record = json.loads(filename.read_text())
            if "payload" in record:
                decoded.extend(self.cache._decode_xmls(record["payload"]))
        self.assertEqual([json.loads(node.text) for node in decoded], [expected])

    def test_missing_auth_never_contacts_provider(self):
        self.properties.clear()
        for operation in (self.client.account, self.client.lists,
                          lambda: self.client.reviews(KEY),
                          lambda: self.client.list_items("selected", "owner"),
                          self.client.favourite_membership):
            self.assertIsNone(operation())
        self.assertFalse(self.calls)

    def test_account_preferences_remain_unknown_when_missing_null_or_invalid(self):
        for preference in (None, True, "1", 4, -1):
            with self.subTest(preference=preference):
                self.clock += 1000
                self.respond(dict(ACCOUNT, profile={"mediaReviewsVisibility": preference}))
                self.assertIsNone(self.client.review_preference())

    def test_http_graphql_schema_and_decode_errors_are_unavailable(self):
        for body, status in (({}, 401), ({}, 302),
                             ({"errors": [{"message": "denied"}], "data": {
                                 "metadataReviewsV2": connection()}}, 200),
                             ({"data": None}, 200),
                             ({"data": {"metadataReviewsV2": {}}}, 200),
                             (["wrong root"], 200)):
            with self.subTest(body=body, status=status):
                self.respond(body, status)
                self.assertIsNone(self.client.reviews(KEY))
        self.responses.append(types.SimpleNamespace(status_code=200, content=b"not JSON"))
        self.assertIsNone(self.client.reviews(KEY))
        self.responses.append(OSError("transport failed"))
        self.assertIsNone(self.client.reviews(KEY))

    def test_empty_reviews_are_success_and_requests_use_fixed_read_boundary(self):
        self.respond_reviews()
        self.assertEqual(self.client.reviews(KEY), connection())
        url, options = self.calls[0]
        self.assertEqual(url, "https://community.plex.tv/api")
        self.assertEqual(options["action_type"], "POST")
        self.assertFalse(options["authenticate"])
        self.assertFalse(options["allow_redirects"])
        self.assertTrue(options["return_response"])
        self.assertEqual(options["timeout"], (3, 5))
        self.assertEqual(options["headerOptions"]["X-Plex-Token"], "account-one")
        self.assertEqual(options["headerOptions"]["Content-Type"], "application/json")
        variables = json.loads(options["postBody"])["variables"]
        self.assertEqual(variables, {"metadataID": KEY, "type": "RECENT",
                                     "first": 24, "after": None})

    def test_malformed_graphql_errors_field_is_unavailable(self):
        for errors in ({}, "", None, False, 0):
            self.respond({"errors": errors,
                          "data": {"metadataReviewsV2": connection()}})
            self.assertIsNone(self.client.reviews(KEY))

    def test_nonstandard_json_constants_are_schema_errors(self):
        payload = json.dumps({"data": {"metadataReviewsV2": connection()},
                              "extensions": {"invalid": float("nan")}})
        self.responses.append(types.SimpleNamespace(status_code=200, content=payload.encode()))
        self.assertIsNone(self.client.reviews(KEY))

    def test_account_uses_fixed_user_get(self):
        self.respond(ACCOUNT)
        self.client.account()
        self.assertEqual(self.calls[0][0], "https://plex.tv/api/v2/user")
        self.assertEqual(self.calls[0][1]["action_type"], "GET")

    def test_review_spoiler_rating_author_and_metadata_survive_normalization(self):
        node = review()
        self.respond_reviews([node], True, "cursor-one")
        result = self.client.reviews(KEY)
        self.assertEqual(result, connection([node], True, "cursor-one"))

    def test_wrong_title_metadata_never_reaches_cache(self):
        self.respond_reviews([review(OTHER)])
        self.assertIsNone(self.client.reviews(KEY))
        self.assertFalse(list(Path(self.profile).rglob("entries/*.json")))

    def test_malformed_ratings_fail_without_numeric_overflow(self):
        for rating in (10 ** 400, True, "8", -1, 11):
            self.respond_reviews([dict(review(), reviewRating=rating)])
            self.assertIsNone(self.client.reviews(KEY))

    def test_invalid_local_ids_and_cursors_never_contact_provider(self):
        for key in ("1234", "../movie", "", None):
            self.assertIsNone(self.client.reviews(key))
        for cursor in ("", "\n", "x" * 2049, 7, {}):
            self.assertIsNone(self.client.reviews(KEY, cursor))
        self.assertFalse(self.calls)

    def test_cursor_must_advance_and_failed_page_can_retry(self):
        self.respond_reviews([review()], True, "cursor-one")
        self.assertIsNone(self.client.reviews(KEY, "cursor-one"))
        self.respond_reviews([review()], True, "cursor-two")
        result = self.client.reviews(KEY, "cursor-one")
        self.assertEqual(result["pageInfo"]["endCursor"], "cursor-two")
        self.assertEqual(json.loads(self.calls[-1][1]["postBody"])["variables"]["after"],
                         "cursor-one")

    def test_invalid_connection_never_looks_empty(self):
        for value in ({"nodes": None, "pageInfo": {}},
                      connection([], True, "next"), connection([], True, None),
                      connection([], "false", None),
                      connection([review()] * 25)):
            self.respond({"data": {"metadataReviewsV2": value}})
            self.assertIsNone(self.client.reviews(KEY))

    def test_oversized_response_is_unavailable(self):
        self.responses.append(types.SimpleNamespace(status_code=200,
                                                    content=b" " * (512 * 1024 + 1)))
        self.assertIsNone(self.client.reviews(KEY))

    def test_account_switch_during_download_discards_response_and_cache(self):
        def switch():
            self.properties["plex_token"] = "account-two"
            return types.SimpleNamespace(status_code=200,
                                         content=json.dumps(ACCOUNT).encode())
        self.responses.append(switch)
        self.assertIsNone(self.client.account())
        self.assertFalse(list(Path(self.profile).rglob("entries/*.json")))

    def test_account_switch_during_cache_read_or_write_rejects_results(self):
        self.respond_reviews([review()])
        self.assertIsNotNone(self.client.reviews(KEY))
        original_read = self.cache.read_xml

        def switch_read(*args, **kwargs):
            value = original_read(*args, **kwargs)
            self.properties["plex_token"] = "account-two"
            return value
        with patch.object(self.cache, "read_xml", switch_read):
            self.assertIsNone(self.client.reviews(KEY))
        self.assertEqual(len(self.calls), 1)
        original_put = self.cache.put_xml

        def switch_write(*args, **kwargs):
            self.properties["plex_token"] = "account-three"
            return original_put(*args, **kwargs)
        self.respond_reviews([review()])
        with patch.object(self.cache, "put_xml", switch_write):
            self.assertIsNone(self.client.reviews(KEY))
        self.assertEqual(len(list(Path(self.profile).rglob("entries/*.json"))), 1)

    def test_switch_between_list_account_lookup_and_post_rejects_old_identity(self):
        self.respond(ACCOUNT)
        self.client.account()
        original = self.client.account

        def switch():
            value = original()
            self.properties["plex_token"] = "account-two"
            return value
        self.client.account = switch
        self.assertIsNone(self.client.lists())
        self.assertEqual(len(self.calls), 1)

    def test_stale_reviews_never_render_or_enqueue_get_refresh(self):
        self.respond_reviews([review()])
        self.assertIsNotNone(self.client.reviews(KEY))
        self.clock += 100000
        self.respond({}, 503)
        self.assertIsNone(self.client.reviews(KEY))
        self.assertFalse(list(Path(self.profile).rglob("refresh/*")))

    def test_cache_separates_titles_pages_and_accounts(self):
        for key, cursor in ((KEY, None), (OTHER, None), (KEY, "next")):
            self.respond_reviews([review(key)])
            self.assertIsNotNone(self.client.reviews(key, cursor))
        self.properties["plex_token"] = "account-two"
        self.respond({}, 403)
        self.assertIsNone(self.client.reviews(KEY))
        self.assertEqual(len(self.calls), 4)

    def test_lists_preserve_empty_and_attach_current_owner(self):
        self.respond(ACCOUNT)
        self.respond({"data": {"userV2": {"customLists": connection()}}})
        self.assertEqual(self.client.lists(), connection())
        self.respond({"data": {"userV2": {"customLists": connection([
            {"id": "list-one", "name": "Selected", "slug": "selected", "itemCount": 2}])}}})
        result = self.client.lists("next")
        self.assertEqual(result["nodes"], [dict(SELECTION, itemCount=2)])
        self.assertEqual(json.loads(self.calls[-1][1]["postBody"])["variables"]["user"],
                         {"id": ACCOUNT["uuid"]})

    def test_lists_and_list_contents_distinguish_errors_from_empty(self):
        self.respond(ACCOUNT)
        self.respond({"errors": [{"message": "denied"}], "data": None})
        self.assertIsNone(self.client.lists())
        self.respond({"data": {"userV2": {"customLists": connection()}}})
        self.assertEqual(self.client.lists(), connection())
        self.respond({"errors": [{"message": "missing"}], "data": None})
        self.assertIsNone(self.client.list_items("selected", "owner"))
        self.respond(list_page())
        self.assertEqual(self.client.list_items("selected", "owner")["nodes"], [])

    def test_account_and_list_caches_exclude_stale_records(self):
        self.respond(ACCOUNT)
        self.respond({"data": {"userV2": {"customLists": connection()}}})
        self.assertIsNotNone(self.client.lists())
        self.respond(list_page())
        self.assertIsNotNone(self.client.list_items("selected", "owner"))
        self.clock += 100000
        self.respond({}, 503)
        self.assertIsNone(self.client.account())
        self.respond({}, 503)
        self.assertIsNone(self.client.list_items("selected", "owner"))
        self.respond(ACCOUNT)
        self.respond({}, 503)
        self.assertIsNone(self.client.lists())
        self.assertFalse(list(Path(self.profile).rglob("refresh/*")))

    def test_list_nodes_preserve_order_and_unsupported_types(self):
        nodes = [{"id": OTHER, "type": "EPISODE", "title": "Episode"},
                 {"id": KEY, "type": "FUTURE_TYPE", "title": "Future"}]
        self.respond(list_page(nodes, count=2))
        result = self.client.list_items("selected", "owner")
        self.assertEqual(result, {"id": "list-one", "name": "Selected",
                                  "slug": "selected", "itemCount": 2,
                                  **connection(nodes)})

    def test_save_rejects_an_account_change_during_selection_read(self):
        expected = self.cache.active_account_hash()
        original = self.client._selections
        def switch():
            result = original()
            self.properties["plex_token"] = "account-two"
            return result
        self.client._selections = switch
        self.assertFalse(self.client.save_selected_list(SELECTION, expected))
        self.assertEqual(self.settings, {})

    def test_save_rejects_changed_or_missing_expected_account(self):
        original = self.cache.active_account_hash()
        self.properties["plex_token"] = "account-two"
        current = self.cache.active_account_hash()
        self.settings["plex_favourite_lists"] = json.dumps({current: SELECTION})
        before = dict(self.settings)
        for selection in (None, SELECTION):
            for expected in (original, None, ""):
                self.assertFalse(self.client.save_selected_list(selection, expected))
                self.assertEqual(self.settings, before)
        self.properties.clear()
        self.assertFalse(self.client.save_selected_list(SELECTION, original))
        self.assertEqual(self.settings, before)

    def test_selected_list_is_validated_and_scoped_without_account_network(self):
        self.assertTrue(self.client.save_selected_list(SELECTION, self.cache.active_account_hash()))
        self.assertEqual(self.client.selected_list(), SELECTION)
        self.properties["plex_token"] = "account-two"
        self.assertIsNone(self.client.selected_list())
        second = dict(SELECTION, id="list-two", slug="second")
        self.assertTrue(self.client.save_selected_list(second, self.cache.active_account_hash()))
        self.assertTrue(self.client.save_selected_list(None, self.cache.active_account_hash()))
        self.properties["plex_token"] = "account-one"
        self.assertEqual(self.client.selected_list(), SELECTION)
        self.assertFalse(self.calls)

    def test_malformed_selections_are_rejected_without_destroying_other_accounts(self):
        self.client.save_selected_list(SELECTION, self.cache.active_account_hash())
        for value in ({}, [], dict(SELECTION, id=""), dict(SELECTION, slug="../path"),
                      dict(SELECTION, username=""), dict(SELECTION, name="\n")):
            self.assertFalse(self.client.save_selected_list(value, self.cache.active_account_hash()))
            self.assertEqual(self.client.selected_list(), SELECTION)
        key = self.cache.active_account_hash()
        self.settings["plex_favourite_lists"] = json.dumps({key: {"id": "bad"}})
        self.assertIsNone(self.client.selected_list())
        self.settings["plex_favourite_lists"] = "broken JSON"
        self.assertIsNone(self.client.selected_list())

    def test_complete_membership_fetches_all_pages_and_caches_complete_set(self):
        self.client.save_selected_list(SELECTION, self.cache.active_account_hash())
        self.respond(list_page([{"id": KEY, "type": "MOVIE", "title": "One"}],
                               True, "next", 2))
        self.respond(list_page([{"id": OTHER, "type": "EPISODE", "title": "Two"}], count=2))
        self.assertEqual(self.client.favourite_membership(), {KEY, OTHER})
        self.assertEqual(self.client.favourite_membership(), {KEY, OTHER})
        self.assertEqual(len(self.calls), 2)

    def test_membership_failure_and_count_mismatch_never_confirm_absence(self):
        self.client.save_selected_list(SELECTION, self.cache.active_account_hash())
        self.respond(list_page([{"id": KEY, "type": "MOVIE", "title": "One"}],
                               True, "next", 2))
        self.respond({}, 503)
        self.assertIsNone(self.client.favourite_membership())
        self.respond(list_page([], count=2))
        self.assertIsNone(self.client.favourite_membership())

    def test_membership_rejects_cursor_cycles_and_page_limit(self):
        self.client.save_selected_list(SELECTION, self.cache.active_account_hash())
        for index, cursor in enumerate(("one", "two", "one")):
            self.respond(list_page([{"id": "%024x" % (index + 1), "type": "MOVIE",
                                     "title": "Node"}], True, cursor, 30))
        self.assertIsNone(self.client.favourite_membership())
        self.clock += 100000
        for index in range(20):
            self.respond(list_page([{"id": "%024x" % (index + 1), "type": "MOVIE",
                                     "title": "Node"}], True, "cursor-%d" % index, 30))
        self.assertIsNone(self.client.favourite_membership())
        self.assertEqual(len(self.calls), 23)

    def test_membership_empty_is_confirmed_only_for_complete_empty_list(self):
        self.assertIsNone(self.client.favourite_membership())
        self.client.save_selected_list(SELECTION, self.cache.active_account_hash())
        self.respond(list_page())
        self.assertEqual(self.client.favourite_membership(), set())

    def test_membership_rejects_duplicates_wrong_list_and_changed_account(self):
        self.client.save_selected_list(SELECTION, self.cache.active_account_hash())
        duplicate = {"id": KEY, "type": "MOVIE", "title": "One"}
        self.respond(list_page([duplicate, duplicate], count=2))
        self.assertIsNone(self.client.favourite_membership())
        self.clock += 100000
        wrong = list_page()
        wrong["data"]["customListBySlug"]["id"] = "another-list"
        self.respond(wrong)
        self.assertIsNone(self.client.favourite_membership())
        self.clock += 100000

        def switch():
            self.properties["plex_token"] = "account-two"
            return types.SimpleNamespace(status_code=200,
                                         content=json.dumps(list_page()).encode())
        self.responses.append(switch)
        self.assertIsNone(self.client.favourite_membership())

    def test_stale_complete_membership_cannot_confirm_absence(self):
        self.client.save_selected_list(SELECTION, self.cache.active_account_hash())
        self.respond(list_page())
        self.assertEqual(self.client.favourite_membership(), set())
        self.clock += 100000
        self.respond({}, 503)
        self.assertIsNone(self.client.favourite_membership())


if __name__ == "__main__":
    unittest.main()
