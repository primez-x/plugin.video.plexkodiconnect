"""Failure cases for Plex review and List directory integration."""

import types
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
        self.module._discover_cards = lambda *args: [('route', {'extraproperties': {'ratingKey': KEY}}, False)]
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


if __name__ == "__main__":
    unittest.main()
