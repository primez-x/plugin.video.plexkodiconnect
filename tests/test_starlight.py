"""Plex catalogue contract failures: unsafe paths, stale people, wrong local IDs.

Fixtures contain public Plex catalogue data with account and signed data removed.
"""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('resources.lib.starlight_contract', ROOT / 'resources/lib/starlight.py')


def fixture(name):
    return json.loads((ROOT / 'tests/fixtures/starlight_{}.json'.format(name)).read_text())['MediaContainer']


class StarlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(cls.provider)

    def test_route_parser_rejects_unsafe_or_unknown_routes(self):
        for path in ('/starlight/../person', '/starlight/%2e%2e/person', '/starlight/unknown', '/elsewhere/search'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.provider.parse_route('plugin://plugin.video.plexkodiconnect'+path, {})

    def test_route_parser_validates_identity_and_watchlist_action(self):
        for params in ({'rating_key': '../secret'}, {'rating_key': 'abc', 'action': 'delete'}, {}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.provider.parse_route('plugin://plugin.video.plexkodiconnect/starlight/watchlist', params)
        self.assertEqual(self.provider.parse_route('plugin://plugin.video.plexkodiconnect/starlight/search', {'query':'Alien'})[0], 'search')

    def test_person_route_preserves_the_name_query(self):
        self.assertEqual(
            self.provider.route('person', name='Leonardo DiCaprio', request='current'),
            'plugin://plugin.video.plexkodiconnect/starlight/person?name=Leonardo+DiCaprio&request=current')

    def test_watchlist_route_queues_without_an_open_detail_page(self):
        from test_discover_listing import load_entrypoint
        entrypoint, _, _, _ = load_entrypoint()
        import resources.lib as package
        app, transfer, watchlist = Mock(), Mock(), Mock()
        watchlist.begin_key.return_value = None
        with patch.object(package, 'app', app, create=True), \
                patch.object(package, 'transfer', transfer, create=True), \
                patch.object(package, 'watchlist', watchlist, create=True), \
                patch.object(sys, 'argv', ['plugin://plugin.video.plexkodiconnect', '-1']):
            self.provider.dispatch(self.provider.route('watchlist'),
                                   {'rating_key':'abc123', 'action':'add', 'type':'movie'})
        transfer.plex_command.assert_called_once_with(
            'WATCHLIST_ADD_KEY?rating_key=abc123&plex_type=movie')

    def test_watchlist_route_preserves_live_detail_projection(self):
        from test_discover_listing import load_entrypoint
        load_entrypoint()
        import resources.lib as package
        app, transfer, watchlist = Mock(), Mock(), Mock()
        watchlist.begin_key.return_value = {'rating_key':'abc123', 'desired':'present',
                                           'request_id':'current'}
        with patch.object(package, 'app', app, create=True), \
                patch.object(package, 'transfer', transfer, create=True), \
                patch.object(package, 'watchlist', watchlist, create=True), \
                patch.object(sys, 'argv', ['plugin://plugin.video.plexkodiconnect', '-1']):
            self.provider.dispatch(self.provider.route('watchlist'),
                                   {'rating_key':'abc123', 'action':'add', 'type':'movie'})
        transfer.plex_command.assert_called_once_with(
            'WATCHLIST_DETAIL_KEY?rating_key=abc123&desired=present&request_id=current')

    def test_watchlist_actions_use_encoded_canonical_keys(self):
        route = self.provider.route('watchlist', rating_key='abc123', action='add')
        self.assertEqual(route, 'plugin://plugin.video.plexkodiconnect/starlight/watchlist?rating_key=abc123&action=add')
        self.assertEqual(self.provider.watchlist_request('abc123', 'remove'), {'rating_key':'abc123', 'plex_type':'movie'})

    def test_dbid_resolution_reuses_the_native_mapper_and_online_filter(self):
        from test_discover_listing import load_entrypoint
        entrypoint, _, _, _ = load_entrypoint()
        entrypoint.API.ratingtype = lambda self: None
        entrypoint.listing_timer.mark = lambda *args: None
        calls = []

        class LocalAPI:
            def __init__(self, xml):
                self.xml = xml
                self.plex_type = xml.get('type')

        def resolve(records, check_by_guid=False, match_external_ids=False):
            calls.append((check_by_guid, match_external_ids))
            return [LocalAPI(record) for record in records]

        entrypoint.mass_api = resolve
        entrypoint.widgets.generate_item = lambda api: {
            'title': api.xml.get('title'), 'type': api.plex_type,
            'file': 'plugin://plugin.video.plexkodiconnect.movies/?plex_id=9&mode=play',
            'extraproperties': {'DBID': '42'}}
        records = fixture('hub')['Metadata'][:1]
        cards = self.provider.build_cards(records, 'catalogue')
        self.assertEqual(cards[0][1]['extraproperties']['DBID'], '42')
        self.assertEqual(cards[0][1]['extraproperties']['Starlight.InLibrary'], 'true')
        self.assertIn('plex_id=9', cards[0][0])
        self.assertIn((True, True), calls)
        self.assertEqual(self.provider.build_cards(records, 'catalogue', online=True), [])

    def test_provider_only_cards_open_info_and_supply_board_and_watchlist_paths(self):
        from test_discover_listing import load_entrypoint
        entrypoint, _, _, _ = load_entrypoint()
        entrypoint.API.ratingtype = lambda self: None
        entrypoint.listing_timer.mark = lambda *args: None
        records = fixture('hub')['Metadata']
        records[0]['_role'] = 'Executive Producer'
        cards = self.provider.build_cards(records, 'catalogue')
        self.assertEqual(cards[0][1]['label2'], 'Executive Producer')
        self.assertEqual(len(cards), 5)
        for rank, (path, card, folder) in enumerate(cards, 1):
            props = card['extraproperties']
            self.assertTrue(path.startswith(self.provider.PREFIX+'item?'))
            self.assertFalse(folder)
            self.assertEqual(card['IsPlayable'], 'false')
            self.assertEqual(props['Starlight.BoardRank'], str(rank))
            self.assertEqual(props['Starlight.PrimaryLabel'], 'Add to watchlist')
            self.assertIn('action=add', props['Starlight.PrimaryAction'])

    def test_hub_conversion_preserves_order_identity_and_nested_art(self):
        source = fixture('hub')['Metadata']
        records = self.provider.metadata(source)
        self.assertEqual([r.get('ratingKey') for r in records], [r['ratingKey'] for r in source])
        self.assertEqual(records[0].find('Image').get('type'), 'background')
        self.assertEqual(self.provider.artwork(source[0])['clearlogo'], source[0]['Image'][3]['url'])

    def test_credits_merge_roles_deduplicate_and_sort_newest_first(self):
        records = self.provider.credits(fixture('credits'))
        self.assertEqual(len({r['ratingKey'] for r in records}), len(records))
        self.assertEqual([r['year'] for r in records], sorted([r['year'] for r in records], reverse=True))
        self.assertTrue(all(r['_role'] for r in records))
        self.assertTrue(all(r['guid'].startswith('plex://') for r in records))

    def test_search_separates_titles_and_people(self):
        titles, people = self.provider.search_records(fixture('search'))
        self.assertTrue(titles)
        self.assertTrue(people)
        self.assertTrue(all(r['type'] in ('movie','show') for r in titles))
        self.assertTrue(all(r['type']=='person' for r in people))

    def test_batch_metadata_adds_external_ids_without_changing_source_order_or_roles(self):
        records = fixture('hub')['Metadata'][:2]
        records[0]['_role'] = 'Actor'
        enriched = [dict(r, Guid=[{'id': 'tmdb://123'}], summary='Plex summary', userState=True) for r in reversed(records)]
        with patch.object(self.provider, '_fetch', return_value={'Metadata': enriched}) as fetch:
            result = self.provider.hydrate(records)
        self.assertEqual([r['ratingKey'] for r in result], [r['ratingKey'] for r in records])
        self.assertEqual(result[0]['_role'], 'Actor')
        self.assertEqual(result[0]['Guid'], [{'id':'tmdb://123'}])
        self.assertTrue(result[0]['userState'])
        self.assertIn('includeGuids=1', fetch.call_args[0][0])

    def test_artwork_rejects_credentials_and_unsafe_schemes(self):
        self.assertEqual(self.provider.artwork({'thumb':'https://example.org/a?X-Plex-Token=secret','art':'file:///private'}), {})
        self.assertEqual(self.provider.safe_image('https://user:secret@example.org/p.jpg'), '')
        self.assertEqual(self.provider.safe_image('https://example.org/"injection'), '')

    def test_present_watchlist_hint_selects_remove_action(self):
        from test_discover_listing import load_entrypoint
        entrypoint, _, _, _ = load_entrypoint()
        entrypoint.API.ratingtype = lambda self: None
        entrypoint.listing_timer.mark = lambda *args: None
        entrypoint.discover_cache.source_is_current = lambda url: False
        records = fixture('hub')['Metadata'][:1]
        records[0]['userState'] = True
        props = self.provider.build_cards(records, 'catalogue')[0][1]['extraproperties']
        self.assertEqual(props['Starlight.PrimaryLabel'], 'Remove from watchlist')
        self.assertIn('action=remove', props['Starlight.PrimaryAction'])

    def test_person_properties_use_trusted_routes_and_plain_biography(self):
        person = fixture('person')['Metadata'][0]
        fields = self.provider.person_properties(person)
        self.assertIn('1996-09-01', fields['Meta'])
        self.assertIn('Oakland', fields['Meta'])
        self.assertTrue(fields['CreditsPath'].startswith(self.provider.PREFIX))
        self.assertTrue(fields['ImagesPath'].startswith(self.provider.PREFIX))
        self.assertEqual(fields['Biography'], person['summary'])

    def test_person_update_cannot_overwrite_a_new_request(self):
        class Window:
            fields = {'Starlight.Person.Request':'new'}
            def getProperty(self, key): return self.fields.get(key, '')
            def setProperty(self, key, value): self.fields[key] = value
        window = Window()
        self.assertFalse(self.provider.publish_person(window, 'old', {'Biography':'wrong'}))
        self.assertNotIn('Starlight.Person.Biography', window.fields)

    def test_online_info_initializes_the_existing_watchlist_status_contract(self):
        from test_discover_listing import load_entrypoint
        entrypoint, _, _, _ = load_entrypoint()
        import resources.lib as package
        gui, transfer, watchlist, app = Mock(), Mock(), Mock(), Mock()
        watchlist.DETAIL_IDENTITY = 'PKC.Watchlist.Detail.Identity'
        watchlist.DETAIL_STATE = 'PKC.Watchlist.Detail.State'
        watchlist.DETAIL_PENDING = 'PKC.Watchlist.Detail.Pending'
        card = {'extraproperties': {'Starlight.InLibrary':'false', 'PlexWatchlistHint':'absent'},
                'type':'movie'}
        with patch.dict(sys.modules, {'xbmcgui':gui}), \
                patch.object(package, 'transfer', transfer, create=True), \
                patch.object(package, 'watchlist', watchlist, create=True), \
                patch.object(package, 'app', app, create=True), \
                patch.object(self.provider, '_fetch', return_value={'Metadata':[]}), \
                patch.object(self.provider, 'build_cards', return_value=[('',card,False)]):
            self.provider.dispatch(self.provider.route('item'), {'rating_key':'abc123'})
        gui.Window.return_value.setProperty.assert_any_call('PKC.Watchlist.Detail.Identity', 'plex.abc123')
        transfer.plex_command.assert_called_once_with(
            'WATCHLIST_STATUS_KEY?rating_key=abc123&plex_type=movie&watchlist_hint=absent')

    def test_requested_person_never_reopens_the_page(self):
        from test_discover_listing import load_entrypoint
        entrypoint, _, _, _ = load_entrypoint()
        window = Mock()
        window.getProperty.return_value = 'current'
        xbmc = Mock()
        gui = Mock()
        gui.Window.return_value = window
        for records in ([], fixture('person')['Metadata']):
            with self.subTest(found=bool(records)):
                xbmc.reset_mock()
                with patch.dict(sys.modules, {'xbmc': xbmc, 'xbmcgui': gui}), \
                        patch.object(self.provider, '_fetch', return_value={'Metadata': records}):
                    self.provider._person({'person_key': '1234567890abcdef12345678',
                                           'request': 'current'})
                xbmc.executebuiltin.assert_not_called()
        with patch.dict(sys.modules, {'xbmc': xbmc, 'xbmcgui': gui}), \
                patch.object(self.provider, '_fetch', return_value={}):
            self.provider._person({'name': 'No exact match', 'request': 'current'})
        xbmc.executebuiltin.assert_not_called()

    def test_empty_image_gallery_uses_public_person_portraits(self):
        images = self.provider.person_images({}, fixture('person')['Metadata'][0])
        self.assertEqual(len(images), 2)
        self.assertTrue(all(self.provider.safe_image(r['url']) for r in images))

    def test_trailer_fallback_encodes_title_and_year(self):
        self.assertEqual(self.provider.smarttube_trailer('A & B', 2026), 'plugin://plugin.video.smarttube/trailer?title=A+%26+B&year=2026')

    def test_trailer_resolver_returns_a_nested_plugin_path_to_kodi(self):
        xbmc = types.SimpleNamespace(Player=lambda: None)
        resolved = []
        plugin = types.SimpleNamespace(setResolvedUrl=lambda *args: resolved.append(args))
        gui = types.SimpleNamespace(ListItem=lambda **args: args)
        with patch.dict(sys.modules, {'xbmc':xbmc, 'xbmcplugin':plugin, 'xbmcgui':gui}), patch.object(sys, 'argv', ['plugin://pkc', '7', '']):
            self.provider.play_url('plugin://plugin.video.smarttube/trailer?title=Alien')
        self.assertEqual(resolved, [(7, True, {'path':'plugin://plugin.video.smarttube/trailer?title=Alien'})])

    def test_explicit_native_trailer_bypasses_the_extras_directory_preference(self):
        from test_discover_listing import load_entrypoint
        entrypoint, _, _, _ = load_entrypoint()
        for key in ('REGEX_IMDB', 'REGEX_TVDB', 'REGEX_TMDB', 'REGEX_ANIDB'):
            setattr(entrypoint.utils, key, None)
        entrypoint.utils.cast = lambda kind, value: kind(value)
        entrypoint.app.SYNC = types.SimpleNamespace(show_extras_instead_of_playing_trailer=True)
        entrypoint.v.PLEX_TYPE_CLIP = 'clip'
        import xml.etree.ElementTree as ET
        spec = importlib.util.spec_from_file_location('resources.lib.plex_api.base_contract', ROOT / 'resources/lib/plex_api/base.py')
        base = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'resources.lib.plex_db':types.SimpleNamespace(PlexDB=object),
                                      'resources.lib.timing':types.SimpleNamespace()}):
            spec.loader.exec_module(base)
        api = base.Base(ET.fromstring('<Video><Extras><Video ratingKey="9" extraType="1" guid="file://local" /></Extras></Video>'))
        self.assertIn('plex_id=9', api.trailer(force_play=True))


if __name__ == '__main__':
    unittest.main()
