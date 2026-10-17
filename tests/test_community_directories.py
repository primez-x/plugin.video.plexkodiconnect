"""Failure cases for Plex review and List directory integration."""

from contextlib import contextmanager
import types
from unittest.mock import patch
import unittest
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit

from test_discover_listing import load_entrypoint


KEY = "5d7768244de0ee001fcc7fed"


class Card:
    def __init__(self, label="", path="", **kwargs):
        self.label, self.path = label, path
        self.properties, self.info, self.art = {}, {}, {}
        self.unique_ids = {}

    def setProperty(self, key, value):
        self.properties[key] = value

    def setInfo(self, kind, values):
        self.info.update(values)

    def setArt(self, values):
        self.art.update(values)

    def getVideoInfoTag(self):
        return types.SimpleNamespace(setUniqueID=lambda value, provider: self.unique_ids.__setitem__(provider, value))

    def setLabel2(self, value):
        self.label2 = value


def connection(nodes, after=None):
    return {"nodes": nodes, "pageInfo": {"endCursor": after, "hasNextPage": bool(after)}}


class CommunityDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.module, self.calls, _, _ = load_entrypoint()
        self.module.ListItem = Card
        self.community = types.SimpleNamespace(
            review_preference=lambda: 1,
            reviews=lambda key, after=None: connection([]),
            lists=lambda after=None: connection([]),
            list_items=lambda slug, username, after=None: None,
            favourite_membership=lambda: None,
        )
        self.module._community = lambda: self.community

    def cards(self):
        return [row[1] for call in self.calls for row in call[1]]

    def review(self, spoilers=False):
        return {"id": "review-one", "message": "Hidden ending", "hasSpoilers": spoilers,
                "reviewRating": 8, "date": "2026-10-17", "metadataItem": {"id": KEY, "type": "MOVIE"},
                "userV2": {"displayName": "Example author", "avatar": ""}}

    def test_membership_publication_keeps_the_original_async_generation(self):
        self.module.utils.window = lambda key: 'active-account'
        projected = []
        self.module.plex_membership = types.SimpleNamespace(
            publish=lambda *args, **kwargs: projected.append(kwargs) or False)
        self.module._publish_membership('watchlist', [], 'active-account', generation='before-request')
        self.assertEqual(projected, [{'account_hash': 'active-account', 'generation': 'before-request', 'keys': None}])

    def test_failed_review_request_fails_the_directory(self):
        self.community.reviews = lambda *args: None
        with self.assertRaises(self.module.ListingException):
            self.module.discover_reviews({"rating_key": KEY})
        self.assertEqual(self.cards(), [])

    def test_provider_response_from_a_previous_account_is_never_cached_or_shown(self):
        active = ["account-a"]
        self.module.discover_cache.active_account_hash = lambda: active[0]
        self.module.discover_cache.account_matches = lambda value: value == active[0]
        stored = []
        self.module.discover_cache.put_xml = lambda *args, **kwargs: stored.append(args)
        def download(*args):
            active[0] = "account-b"
            return [ET.Element("MediaContainer")]
        self.module._download_provider_xmls = download
        result = self.module._provider_xmls("https://metadata.provider.plex.tv/library/metadata/"+KEY,
                                           ("metadata.provider.plex.tv",), "detail")
        self.assertEqual(result, [])
        self.assertEqual(stored, [])

    def test_disabled_account_reviews_issue_no_content_request(self):
        self.community.review_preference = lambda: 3
        self.community.reviews = lambda *args: self.fail("Disabled reviews made a request")
        self.module.discover_reviews({"rating_key": KEY})
        self.assertEqual(self.cards(), [])

    def test_spoiler_message_stays_out_of_visible_card_metadata(self):
        self.community.reviews = lambda *args: connection([self.review(True)])
        self.module.discover_reviews({"rating_key": KEY})
        card = self.cards()[0]
        self.assertNotIn("Hidden ending", repr(vars(card)))
        self.assertEqual(card.properties["PlexReviewSpoiler"], "true")

    def test_review_continuation_retains_the_exact_title_and_cursor(self):
        self.community.reviews = lambda *args: connection([self.review()], "cursor+two/=")
        self.module.discover_reviews({"rating_key": KEY})
        path, card, folder = self.calls[0][1][-1]
        params = parse_qs(urlsplit(path).query)
        self.assertTrue(folder)
        self.assertEqual(params["after"], ["cursor+two/="])
        self.assertEqual(params["rating_key"], [KEY])

    def test_failed_list_request_is_not_a_successful_empty_list(self):
        self.community.list_items = lambda *args: None
        with self.assertRaises(self.module.ListingException):
            self.module.discover_list("sample", "Plex")
        self.assertEqual(self.cards(), [])

    def test_list_hydration_preserves_provider_order_and_missing_nodes(self):
        self.community.list_items = lambda *args: dict(
            connection([{"id": "cloud-a", "type": "MOVIE", "title": "First"},
                        {"id": "cloud-b", "type": "MOVIE", "title": "Missing"},
                        {"id": "cloud-c", "type": "MOVIE", "title": "Last"}]), name="Sample")
        root = ET.Element("MediaContainer")
        for key, title in (("cloud-c", "Last"), ("cloud-a", "First")):
            ET.SubElement(root, "Video", ratingKey=key, type="movie", title=title,
                          guid="plex://movie/"+key)
        self.module._provider_xmls = lambda *args, **kwargs: [root]
        self.module.discover_list("sample", "Plex")
        cards = self.cards()
        self.assertEqual([card.get("label") if isinstance(card, dict) else card.label
                          for card in cards], ["First", "Missing", "Last"])
        missing = cards[1]
        self.assertEqual(missing.properties["IsPlayable"], "false")

    def test_a_card_conversion_failure_preserves_the_list_identity(self):
        self.community.list_items = lambda *args: dict(
            connection([{'id': KEY, 'type': 'MOVIE', 'title': 'Unavailable example'}]), name='Sample')
        self.module._provider_xmls = lambda *args, **kwargs: [ET.Element('MediaContainer')]
        self.module._discover_cards = lambda *args, **kwargs: [('route', {'extraproperties': {'ratingKey': KEY}}, False)]
        self.module.widgets.prepare_listitem = lambda value: value
        self.module.widgets.create_listitem = lambda value: None

        self.module.discover_list('sample', 'Plex')

        self.assertEqual(len(self.cards()), 1)
        card = self.cards()[0]
        self.assertEqual(card.label, 'Unavailable example')
        self.assertEqual(card.properties['IsPlayable'], 'false')
        self.assertEqual(card.properties['ratingKey'], KEY)
        self.assertEqual(card.unique_ids['plex'], 'plex://movie/' + KEY)

    def test_a_numeric_pms_key_never_reaches_the_review_api(self):
        self.community.reviews = lambda *args: self.fail("PMS key reached cloud reviews")
        with self.assertRaises(self.module.ListingException):
            self.module.discover_reviews({"rating_key": "123"})

    def test_spoiler_dialog_requires_explicit_confirmation(self):
        self.community.reviews = lambda *args: connection([self.review(True)])
        shown = []
        self.module.xbmcgui.Dialog = lambda: types.SimpleNamespace(
            yesno=lambda *args, **kwargs: False,
            textviewer=lambda *args, **kwargs: shown.append(args))
        self.module.discover_review_show({"rating_key": KEY, "review_id": "review-one"})
        self.assertEqual(shown, [])


class CommunityAccountBoundaryTests(unittest.TestCase):
    @contextmanager
    def operation(self):
        from test_plex_community import CommunityTests

        module, calls, _, _ = load_entrypoint()
        boundary = CommunityTests()
        boundary.setUp()
        module.ListItem = Card
        module.discover_cache = boundary.cache
        boundary.client.utils.extend_url = module.utils.extend_url
        module.utils = boundary.client.utils
        module._community = lambda: boundary.client
        effects = []
        module.plex_membership = types.SimpleNamespace(
            generation=lambda: "generation",
            publish_keys=lambda *args, **kwargs: effects.append((args, kwargs)))
        import resources.lib as package
        boundary.stack.enter_context(patch.object(package, "plex_membership", module.plex_membership))
        module.xbmc.executebuiltin = lambda *args: effects.append(args)
        module.xbmcgui.Dialog = lambda: types.SimpleNamespace(
            select=lambda *args: 1, yesno=lambda *args: True,
            textviewer=lambda *args: effects.append(args),
            notification=lambda *args: None)
        try:
            yield module, boundary, calls, effects
        finally:
            boundary.doCleanups()

    def switch_after(self, boundary, function, token="account-two"):
        def switch(*args, **kwargs):
            result = function(*args, **kwargs)
            boundary.properties["plex_token"] = token
            return result
        return switch

    def queue_lists(self, boundary, more=False):
        from test_plex_community import ACCOUNT, SELECTION, connection

        boundary.respond(ACCOUNT)
        boundary.respond({"data": {"userV2": {"customLists": connection(
            [dict(SELECTION, itemCount=0)], more, "next" if more else None)}}})

    def test_selection_preserves_the_original_account_across_pages_dialog_and_save(self):
        for stage in ("pagination", "dialog", "save_entry", "save_return", "signed_out"):
            with self.subTest(stage=stage), self.operation() as (module, boundary, calls, effects):
                self.queue_lists(boundary, more=stage == "pagination")
                if stage == "pagination":
                    boundary.client.lists = self.switch_after(boundary, boundary.client.lists)
                elif stage in ("dialog", "signed_out"):
                    dialog = module.xbmcgui.Dialog()
                    dialog.select = self.switch_after(
                        boundary, dialog.select, "" if stage == "signed_out" else "account-two")
                    module.xbmcgui.Dialog = lambda: dialog
                elif stage == "save_entry":
                    original = boundary.client.save_selected_list
                    def save(*args, **kwargs):
                        boundary.properties["plex_token"] = "account-two"
                        return original(*args, **kwargs)
                    boundary.client.save_selected_list = save
                else:
                    boundary.client.save_selected_list = self.switch_after(
                        boundary, boundary.client.save_selected_list)
                module.discover_favourites_select()
                boundary.properties["plex_token"] = "account-two"
                self.assertIsNone(boundary.client.selected_list())
                self.assertEqual(effects, [])
                self.assertEqual(len(boundary.calls), 2)

    def test_same_account_selection_and_cancel_preserve_private_state(self):
        from test_plex_community import SELECTION

        for choice in (1, 0, -1):
            with self.subTest(choice=choice), self.operation() as (module, boundary, calls, effects):
                self.queue_lists(boundary)
                account_hash = boundary.cache.active_account_hash()
                boundary.client.save_selected_list(SELECTION, account_hash)
                dialog = module.xbmcgui.Dialog()
                dialog.select = lambda *args: choice
                module.xbmcgui.Dialog = lambda: dialog
                module.discover_favourites_select()
                self.assertEqual(boundary.client.selected_list(), SELECTION if choice != 0 else None)
                if choice >= 0:
                    self.assertEqual(effects[0], (("favourites", None), {"account_hash": account_hash}))
                else:
                    self.assertEqual(effects, [])

    def test_review_account_changes_abort_before_directory_or_text_publication(self):
        from test_plex_community import ACCOUNT, review

        for show in (False, True):
            for stage in ("preference", "identity", "read", "spoiler", "signed_out"):
                if not show and stage == "spoiler":
                    continue
                with self.subTest(show=show, stage=stage), self.operation() as (module, boundary, calls, effects):
                    boundary.respond(ACCOUNT)
                    boundary.respond_reviews([review()])
                    if stage == "preference":
                        boundary.client.review_preference = self.switch_after(
                            boundary, boundary.client.review_preference)
                    elif stage == "identity":
                        module._review_rating_key = self.switch_after(boundary, module._review_rating_key)
                    elif stage in ("read", "signed_out"):
                        boundary.client.reviews = self.switch_after(
                            boundary, boundary.client.reviews,
                            "" if stage == "signed_out" else "account-two")
                    else:
                        dialog = module.xbmcgui.Dialog()
                        dialog.yesno = self.switch_after(boundary, dialog.yesno)
                        module.xbmcgui.Dialog = lambda: dialog
                    try:
                        function = module.discover_review_show if show else module.discover_reviews
                        function({"rating_key": KEY, "review_id": "review-one"})
                    except module.ListingException:
                        pass
                    self.assertEqual(calls, [])
                    self.assertEqual(effects, [])
                    self.assertEqual(len(boundary.calls), 1 if stage in ("preference", "identity") else 2)

    def test_critic_reviews_reject_account_changes_before_display_or_publication(self):
        from test_plex_community import ACCOUNT

        for show in (False, True):
            for stage in ("read", "publish"):
                with self.subTest(show=show, stage=stage), self.operation() as (module, boundary, calls, effects):
                    boundary.respond(dict(ACCOUNT, profile={"mediaReviewsVisibility": 2}))
                    root = ET.Element("MediaContainer")
                    metadata = ET.SubElement(root, "Video", ratingKey=KEY)
                    ET.SubElement(metadata, "Review", text="Critic review", source="Source", tag="Author")
                    module._provider_xmls = lambda *args: [root]
                    records = module._critic_reviews(KEY)
                    if stage == "read":
                        module._critic_reviews = self.switch_after(boundary, module._critic_reviews)
                    elif show:
                        original = module.xbmcgui.Dialog
                        module.xbmcgui.Dialog = self.switch_after(boundary, original)
                    else:
                        module.xbmcplugin.setContent = self.switch_after(boundary, module.xbmcplugin.setContent)
                    try:
                        function = module.discover_review_show if show else module.discover_reviews
                        function({"rating_key": KEY, "review_id": records[0]["id"], "critic": "true"})
                    except module.ListingException:
                        pass
                    self.assertEqual(calls, [])
                    self.assertEqual(effects, [])

    def test_same_account_spoiler_consent_and_cancel(self):
        from test_plex_community import ACCOUNT, review

        for consent in (True, False):
            with self.subTest(consent=consent), self.operation() as (module, boundary, calls, effects):
                boundary.respond(ACCOUNT)
                boundary.respond_reviews([review()])
                dialog = module.xbmcgui.Dialog()
                dialog.yesno = lambda *args: consent
                module.xbmcgui.Dialog = lambda: dialog
                module.discover_review_show({"rating_key": KEY, "review_id": "review-one"})
                self.assertEqual(effects, [("Reviewer", review()["message"])] if consent else [])

    def test_list_directories_reject_account_changes_during_read_and_hydration(self):
        from test_plex_community import list_page

        for detail in (False, True):
            for stage in ("read", "cards", "publish"):
                with self.subTest(detail=detail, stage=stage), self.operation() as (module, boundary, calls, effects):
                    if detail:
                        boundary.respond(list_page([{"id": KEY, "type": "MOVIE", "title": "Title"}], count=1))
                        module._provider_xmls = lambda *args: [ET.Element("MediaContainer")]
                        function = lambda: module.discover_list("selected", "owner")
                        reader = "list_items"
                        if stage == "cards":
                            module._provider_xmls = self.switch_after(boundary, module._provider_xmls)
                    else:
                        self.queue_lists(boundary)
                        function = module.discover_lists
                        reader = "lists"
                        if stage == "cards":
                            module._community_more = self.switch_after(boundary, module._community_more)
                    if stage == "read":
                        setattr(boundary.client, reader, self.switch_after(boundary, getattr(boundary.client, reader)))
                    elif stage == "publish":
                        module.xbmcplugin.setContent = self.switch_after(boundary, module.xbmcplugin.setContent)
                    try:
                        function()
                    except module.ListingException:
                        pass
                    self.assertEqual(calls, [])

    def test_list_identity_conversion_never_clears_the_new_account_membership(self):
        from test_plex_community import SELECTION, list_page

        with self.operation() as (module, boundary, calls, effects):
            boundary.properties["plex_token"] = "account-two"
            boundary.client.save_selected_list(SELECTION, boundary.cache.active_account_hash())
            boundary.properties["plex_token"] = "account-one"
            boundary.respond(list_page([{"id": KEY, "type": "MOVIE", "title": "Title"}], count=1))
            root = ET.Element("MediaContainer")
            ET.SubElement(root, "Video", ratingKey=KEY, type="movie", title="Title", guid="plex://movie/" + KEY)
            module._provider_xmls = lambda *args: [root]
            module.mass_api = self.switch_after(boundary, module.mass_api)
            try:
                module.discover_list("selected", "owner")
            except module.ListingException:
                pass
            self.assertEqual(effects, [])
            self.assertEqual(calls, [])

    def test_signed_out_operations_never_read_or_publish(self):
        with self.operation() as (module, boundary, calls, effects):
            boundary.properties.clear()
            for function in (module.discover_favourites_select, module.discover_lists,
                             lambda: module.discover_list("selected", "owner"),
                             lambda: module.discover_reviews({"rating_key": KEY}),
                             lambda: module.discover_review_show({"rating_key": KEY})):
                try:
                    function()
                except module.ListingException:
                    pass
            self.assertEqual(boundary.calls, [])
            self.assertEqual(calls, [])
            self.assertEqual(effects, [])


if __name__ == "__main__":
    unittest.main()
