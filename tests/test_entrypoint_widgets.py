import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit


TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_discover_listing import load_entrypoint  # noqa: E402


class WidgetContainerSizeTests(unittest.TestCase):
    def setUp(self):
        self.entrypoint = load_entrypoint()[0]
        self.media_window = False
        self.settings = {'widgetLimit': '30'}
        self.urls = []
        test = self
        entrypoint = self.entrypoint
        entrypoint.xbmc.getCondVisibility = (
            lambda condition: condition == 'Window.IsMedia' and test.media_window)
        entrypoint.utils.settings = lambda key: test.settings.get(key, '')
        entrypoint.utils.cast = lambda func, value: (
            None if value in (None, '') else func(value))
        entrypoint.utils.extend_url = lambda url, params: '%s%s%s' % (
            url, '&' if '?' in url else '?', urlencode(params))
        entrypoint.app.APP = types.SimpleNamespace(fetch_pms_item_number=50)

        class FakeDownloadUtils(object):
            def downloadUrl(self, url, *args, **kwargs):
                test.urls.append(url)
                return ET.Element('MediaContainer')

        entrypoint.DU = FakeDownloadUtils
        entrypoint.show_listing = lambda *args, **kwargs: None

    def _query(self, key):
        self.urls.clear()
        self.entrypoint.browse_plex(key=key, section_id='1')
        return parse_qs(urlsplit(self.urls[0]).query)

    def test_widget_listings_request_a_bounded_container(self):
        for key in ('/library/sections/1/onDeck',
                    '/library/sections/1/recentlyAdded',
                    '/hubs/home/continueWatching',
                    '/hubs/home/recentlyAdded?type=1'):
            query = self._query(key)
            self.assertEqual(query['X-Plex-Container-Start'], ['0'], key)
            self.assertEqual(query['X-Plex-Container-Size'], ['50'], key)

    def test_widget_limit_setting_above_the_fetch_size_wins(self):
        self.settings['widgetLimit'] = '100'

        query = self._query('/library/sections/1/onDeck')

        self.assertEqual(query['X-Plex-Container-Size'], ['100'])

    def test_library_browsing_is_never_truncated(self):
        for key in ('/library/sections/1/all',
                    '/library/sections/1/genre',
                    '/library/sections/1/folder',
                    '/hubs/search'):
            self.assertNotIn('X-Plex-Container-Size', self._query(key), key)

    def test_widget_endpoints_browsed_in_a_media_window_are_not_truncated(self):
        self.media_window = True

        query = self._query('/library/sections/1/recentlyAdded')

        self.assertNotIn('X-Plex-Container-Size', query)


class MalformedListingTests(unittest.TestCase):
    def test_failed_items_are_omitted_from_library_and_discover_lists(self):
        from library_sync import isolate_modules
        isolate_modules(self)
        for route in ('library', 'discover'):
            with self.subTest(route=route):
                entrypoint, calls, _, _ = load_entrypoint()
                xml = ET.Element('MediaContainer')
                for title in ('prepare-fails', 'create-fails', 'valid'):
                    ET.SubElement(xml, 'Video', type='movie', title=title,
                                  ratingKey=title, guid='plex://movie/' + title)
                entrypoint.v.PLEX_TYPE_EPISODE = 'episode'
                entrypoint.API = lambda child: types.SimpleNamespace(
                    xml=child, plex_type='movie', tag='Video')
                if route == 'library':
                    entrypoint.mass_api = lambda children: [
                        entrypoint.API(child) for child in children]
                entrypoint.widgets.prepare_listitem = lambda item, *args: (
                    None if item['title'] == 'prepare-fails' else item)
                created_titles = []

                def create(item):
                    self.assertIsNotNone(item)
                    created_titles.append(item['title'])
                    if item['title'] == 'create-fails':
                        return None
                    return (item['file'], item, False)

                entrypoint.widgets.create_listitem = create
                if route == 'library':
                    entrypoint.show_listing(xml)
                else:
                    entrypoint._provider_xmls = lambda *args: [xml]
                    entrypoint.discover_hub('new-for-you')
                self.assertEqual(created_titles, ['create-fails', 'valid'])
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][2], 1)
                self.assertEqual(calls[0][1][0][1]['title'], 'valid')


if __name__ == '__main__':
    unittest.main()
