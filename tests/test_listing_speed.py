"""
Listing speed: batched Kodi details, lazy requests import, opt-in timing log.
"""
import importlib
import json
import subprocess
import sys
import textwrap
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

KODI_TYPES = dict(
    KODI_TYPE_MOVIE='movie', KODI_TYPE_SET='set', KODI_TYPE_EPISODE='episode',
    KODI_TYPE_SEASON='season', KODI_TYPE_SHOW='tvshow', KODI_TYPE_SONG='song',
    KODI_TYPE_ALBUM='album', KODI_TYPE_ARTIST='artist')
PLEX_TYPES = dict(
    PLEX_TYPE_MOVIE='movie', PLEX_TYPE_EPISODE='episode',
    PLEX_TYPE_SEASON='season', PLEX_TYPE_SHOW='show',
    PLEX_TYPE_ALBUM='album', PLEX_TYPE_ARTIST='artist')


class IsolatedTestCase(unittest.TestCase):
    """Restore sys.modules and the resources.lib package after each test"""

    def setUp(self):
        lib = importlib.import_module('resources.lib')
        saved_modules = dict(sys.modules)
        saved_lib = dict(lib.__dict__)

        def restore():
            sys.modules.clear()
            sys.modules.update(saved_modules)
            lib.__dict__.clear()
            lib.__dict__.update(saved_lib)
        self.addCleanup(restore)


class FakeKodi(object):
    """executeJSONRPC stand-in answering Get*Details single and batch calls"""

    def __init__(self, missing=(), batch_answer=None):
        self.missing = set(missing)
        self.batch_answer = batch_answer
        self.calls = []
        self.threads = set()
        self.lock = threading.Lock()

    def _answer(self, request):
        kodi_type = next(key[:-2] for key in request['params']
                         if key.endswith('id'))
        kodi_id = request['params']['%sid' % kodi_type]
        if (kodi_id, kodi_type) in self.missing:
            return {'jsonrpc': '2.0', 'id': request['id'],
                    'error': {'code': -32602, 'message': 'Invalid params.'}}
        details = {'title': '%s %s' % (kodi_type, kodi_id),
                   '%sid' % kodi_type: kodi_id}
        if kodi_type == 'tvshow':
            details.update(season=2, episode=10, watchedepisodes=4)
        return {'jsonrpc': '2.0', 'id': request['id'],
                'result': {'%sdetails' % kodi_type: details}}

    def __call__(self, query):
        request = json.loads(query)
        with self.lock:
            self.calls.append(request)
            self.threads.add(threading.get_ident())
        if isinstance(request, list):
            if self.batch_answer is not None:
                return self.batch_answer
            # Kodi answers in request order; reverse to prove ids are used
            return json.dumps([self._answer(r) for r in reversed(request)])
        return json.dumps(self._answer(request))


def _set_lib_attributes(**modules):
    """`from . import x` prefers the package attribute over sys.modules"""
    lib = importlib.import_module('resources.lib')
    for name, module in modules.items():
        if module is None:
            lib.__dict__.pop(name, None)
        else:
            setattr(lib, name, module)


def load_json_rpc(fake_kodi):
    _set_lib_attributes(json_rpc=None, timing=None, variables=None,
                        kodi_constants=None)
    for name in ('xbmc', 'resources.lib.json_rpc', 'resources.lib.timing',
                 'resources.lib.variables', 'resources.lib.kodi_constants'):
        sys.modules.pop(name, None)
    xbmc = types.ModuleType('xbmc')
    xbmc.executeJSONRPC = fake_kodi
    sys.modules['xbmc'] = xbmc
    sys.modules['resources.lib.timing'] = types.ModuleType('resources.lib.timing')
    variables = types.ModuleType('resources.lib.variables')
    variables.__dict__.update(KODI_TYPES)
    sys.modules['resources.lib.variables'] = variables
    _set_lib_attributes(timing=sys.modules['resources.lib.timing'],
                        variables=variables)
    return importlib.import_module('resources.lib.json_rpc')


class ItemsDetailsTests(IsolatedTestCase):
    def test_one_batch_call_returns_what_item_details_returns(self):
        kodi = FakeKodi(missing={(3, 'movie')})
        js = load_json_rpc(kodi)
        keys = [(1, 'movie'), (2, 'episode'), (3, 'movie'), (7, 'tvshow')]

        batched = js.items_details(keys)

        self.assertEqual(len(kodi.calls), 1)
        self.assertIsInstance(kodi.calls[0], list)
        single = {key: js.item_details(*key) for key in keys}
        self.assertEqual(batched, single)
        self.assertEqual(batched[(3, 'movie')], {})
        self.assertEqual(batched[(7, 'tvshow')]['extraproperties'], {
            'totalseasons': '2', 'totalepisodes': '10',
            'watchedepisodes': '4', 'unwatchedepisodes': '6'})
        self.assertEqual(kodi.calls[0][0]['params']['properties'],
                         js.JSON_FROM_KODITYPE['movie'][1])

    def test_large_listings_are_split_into_parallel_batches(self):
        kodi = FakeKodi()
        js = load_json_rpc(kodi)
        keys = [(i, 'movie') for i in range(1, 31)]

        details = js.items_details(keys)

        self.assertEqual(len(kodi.calls), js.ITEMS_DETAILS_MAX_WORKERS)
        self.assertTrue(all(isinstance(call, list) for call in kodi.calls))
        self.assertEqual(sorted(len(call) for call in kodi.calls), [6, 8, 8, 8])
        self.assertEqual(set(details), set(keys))
        self.assertTrue(all(details[(i, 'movie')]['title'] == 'movie %s' % i
                            for i in range(1, 31)))

    def test_duplicates_and_unknown_types_are_skipped(self):
        kodi = FakeKodi()
        js = load_json_rpc(kodi)

        details = js.items_details([(1, 'movie'), (1, 'movie'), (5, 'clip')])

        self.assertEqual(list(details), [(1, 'movie')])
        self.assertEqual(len(kodi.calls[0]), 1)
        self.assertEqual(js.items_details([]), {})

    def test_a_rejected_batch_falls_back_to_single_calls(self):
        kodi = FakeKodi(batch_answer=json.dumps(
            {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600}}))
        js = load_json_rpc(kodi)

        details = js.items_details([(1, 'movie'), (2, 'movie')])

        self.assertEqual(details[(1, 'movie')]['title'], 'movie 1')
        self.assertEqual(details[(2, 'movie')]['title'], 'movie 2')
        self.assertEqual(len(kodi.calls), 3)


class FakeApi(object):
    def __init__(self, kodi_id, plex_type='movie', kodi_type='movie',
                 tag='Video'):
        self.kodi_id = kodi_id
        self.plex_type = plex_type
        self.kodi_type = kodi_type
        self.tag = tag


def load_widgets(js):
    for name in ('xbmc', 'xbmcgui', 'xbmcvfs', 'resources.lib.widgets',
                 'resources.lib.artwork_urls', 'resources.lib.utils',
                 'resources.lib.variables', 'resources.lib.json_rpc'):
        sys.modules.pop(name, None)
    for name in ('xbmc', 'xbmcgui', 'xbmcvfs'):
        sys.modules[name] = types.ModuleType(name)
    sys.modules['xbmcgui'].ListItem = object
    sys.modules['resources.lib.json_rpc'] = js
    sys.modules['resources.lib.utils'] = types.ModuleType('resources.lib.utils')
    sys.modules['resources.lib.artwork_urls'] = types.ModuleType(
        'resources.lib.artwork_urls')
    variables = types.ModuleType('resources.lib.variables')
    variables.KODIVERSION = 21
    variables.__dict__.update(KODI_TYPES)
    variables.__dict__.update(PLEX_TYPES)
    sys.modules['resources.lib.variables'] = variables
    _set_lib_attributes(
        widgets=None, json_rpc=js, variables=variables,
        utils=sys.modules['resources.lib.utils'],
        artwork_urls=sys.modules['resources.lib.artwork_urls'])
    return importlib.import_module('resources.lib.widgets')


class WidgetPrefetchTests(IsolatedTestCase):
    def test_provider_identity_reaches_the_native_video_tag(self):
        class Tag:
            unique_ids = None

            def setUniqueIDs(self, values):
                self.unique_ids = values

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        tag = Tag()
        class Item:
            def __init__(self, **kwargs):
                pass

            def getVideoInfoTag(self):
                return tag

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        self.widgets.xbmcgui.ListItem = Item
        identities = {'plex': 'plex://movie/5d7768244de0ee001fcc7fed', 'imdb': 'tt0088763'}
        item = {'type': 'movie', 'file': '', 'extraproperties': {}, 'uniqueid': identities}
        self.assertIsNotNone(self.widgets.create_listitem(item, as_tuple=False, listitem=Item))
        self.assertEqual(tag.unique_ids, identities)

    def setUp(self):
        super().setUp()
        self.batches = []
        self.single = []
        test = self
        js = types.ModuleType('resources.lib.json_rpc')

        def items_details(keys):
            keys = list(keys)
            test.batches.append(keys)
            return {key: {'title': 'batched %s' % key[0]} for key in keys}

        def item_details(kodi_id, kodi_type):
            test.single.append((kodi_id, kodi_type))
            return {'title': 'single %s' % kodi_id}

        js.items_details = items_details
        js.item_details = item_details
        self.widgets = load_widgets(js)

    def test_null_optional_stream_values_do_not_reach_native_setters(self):
        class Detail:
            def __init__(self):
                self.values = {}

            def __getattr__(self, name):
                return lambda value: self.values.__setitem__(name, value)

        cases = (
            ('Video', ('codec', 'aspect', 'width', 'height', 'duration', 'stereomode', 'language', 'hdrtype')),
            ('Audio', ('channels', 'codec', 'language')),
            ('Subtitle', ('language',)),
        )
        for kind, fields in cases:
            setattr(self.widgets.xbmc, kind + 'StreamDetail', Detail)
            create = getattr(self.widgets, '_create_' + kind + 'StreamDetail')
            for field in fields:
                with self.subTest(kind=kind, field=field):
                    detail = create({field: None})
                    self.assertEqual(detail.values, {})
        detail = self.widgets._create_VideoStreamDetail({'aspect': 1.777, 'width': 1920, 'duration': 0})
        self.assertEqual(detail.values, {'setAspect': 1.78, 'setWidth': 1920, 'setDuration': 0})

    def test_prepared_stream_lists_reach_native_details_as_first_stream_dicts(self):
        received = []

        class Detail:
            def __init__(self):
                self.values = {}

            def __getattr__(self, name):
                return lambda value: self.values.__setitem__(name, value)

        class Tag:
            def __init__(self):
                self.streams = []

            def addVideoStream(self, detail):
                self.streams.append(('video', detail.values))

            def addAudioStream(self, detail):
                self.streams.append(('audio', detail.values))

            def addSubtitleStream(self, detail):
                self.streams.append(('subtitle', detail.values))

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        class Item:
            def __init__(self, **kwargs):
                self.tag = Tag()
                self.properties = {}

            def getVideoInfoTag(self):
                return self.tag

            def setProperties(self, values):
                self.properties.update(values)

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        for kind in ('Video', 'Audio', 'Subtitle'):
            setattr(self.widgets.xbmc, kind + 'StreamDetail', Detail)
            create = getattr(self.widgets, '_create_' + kind + 'StreamDetail')
            setattr(self.widgets, '_create_' + kind + 'StreamDetail',
                    lambda stream, create=create: received.append(stream) or create(stream))
        self.widgets.xbmcgui.ListItem = Item
        self.widgets.artwork_urls.normalize_plex_artwork_url = lambda url: url
        # api.mediastreams() answers stream lists, empty for provider-only Watchlist cards
        empty = {'video': [], 'audio': [], 'subtitle': []}
        populated = {
            'video': [{'codec': 'hevc', 'width': 3840, 'height': 2160, 'aspect': 1.777, 'duration': 0},
                      {'codec': 'h264'}],
            'audio': [{'codec': 'eac3', 'channels': 6, 'language': 'eng'}, {'codec': 'aac'}],
            'subtitle': [{'language': 'eng'}, {'language': 'fra'}],
        }
        for streams in (empty, populated):
            with self.subTest(populated=bool(streams['video'])):
                del received[:]
                item = self.widgets.prepare_listitem(
                    {'type': 'movie', 'file': '/watchlist', 'streamdetails': streams})
                listitem = self.widgets.create_listitem(item, as_tuple=False, listitem=Item)
                self.assertIsNotNone(listitem)
                self.assertEqual([type(stream) for stream in received], [dict] * 3)
        self.assertEqual(listitem.tag.streams, [
            ('video', {'setCodec': 'hevc', 'setAspect': 1.78, 'setWidth': 3840,
                       'setHeight': 2160, 'setDuration': 0}),
            ('audio', {'setChannels': 6, 'setCodec': 'eac3', 'setLanguage': 'eng'}),
            ('subtitle', {'setLanguage': 'eng'}),
        ])
        for key, value in (('VideoResolution', '4K'), ('VideoCodec', 'hevc'),
                           ('VideoAspect', '1.78'), ('AudioCodec', 'eac3'),
                           ('AudioChannels', '6'), ('AudioLanguage', 'eng'),
                           ('SubtitleLanguage', 'eng')):
            self.assertEqual(item['extraproperties'][key], value)

    def test_season_landscape_fallback_preserves_poster(self):
        self.widgets.artwork_urls.normalize_plex_artwork_url = lambda url: url
        for media_type in ('episode', 'season'):
            with self.subTest(media_type=media_type):
                result = self.widgets.prepare_listitem({
                    'type': media_type, 'file': '/video.mkv',
                    'art': {'poster': 'poster.jpg',
                            'season.landscape': 'landscape.jpg'}})
                self.assertEqual(result['art']['poster'], 'poster.jpg')
                self.assertEqual(result['art']['landscape'], 'landscape.jpg')

    def test_only_items_generate_item_looks_up_are_prefetched(self):
        apis = [
            FakeApi(1),
            FakeApi(None),
            FakeApi(2, 'show', 'tvshow', tag='Directory'),
            FakeApi(3, 'genre', None, tag='Directory'),
            FakeApi(4, 'episode', 'episode'),
        ]

        self.widgets.prefetch_kodi_details(apis)

        self.assertEqual(self.batches,
                         [[(1, 'movie'), (2, 'tvshow'), (4, 'episode')]])

    def test_prefetched_details_are_used_once_then_looked_up_again(self):
        self.widgets.prefetch_kodi_details([FakeApi(1)])

        first = self.widgets._kodi_item_details(FakeApi(1))
        second = self.widgets._kodi_item_details(FakeApi(1))

        self.assertEqual(first, {'title': 'batched 1'})
        self.assertEqual(second, {'title': 'single 1'})
        self.assertEqual(self.single, [(1, 'movie')])

    def test_a_failed_prefetch_leaves_single_lookups(self):
        def broken(keys):
            raise RuntimeError('boom')
        self.widgets.js.items_details = broken
        self.widgets.LOG.disabled = True
        try:
            self.widgets.prefetch_kodi_details([FakeApi(1)])
        finally:
            self.widgets.LOG.disabled = False

        self.assertEqual(self.widgets._kodi_item_details(FakeApi(1)),
                         {'title': 'single 1'})


class ListingTimerTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        sys.modules.pop('resources.lib.listing_timer', None)
        self.window = {}
        utils = types.ModuleType('resources.lib.utils')
        utils.window = lambda key, value=None, clear=False: (
            self.window.pop(key, None) if clear else
            self.window.__setitem__(key, value) if value is not None else
            self.window.get(key, ''))
        self.settings = {}
        utils.settings = lambda key: self.settings.get(key, '')
        sys.modules['resources.lib.utils'] = utils
        lib = importlib.import_module('resources.lib')
        lib.utils = utils
        self.timer = importlib.import_module('resources.lib.listing_timer')
        self.logged = []
        self.timer.LOG = SimpleNamespace(info=self.logged.append)

    def test_format_sums_repeated_phases_and_reports_the_rest_as_other(self):
        line = self.timer.format_line(
            'mode=discover_hub hub_id=trending', 0.5,
            [('import', 0.2), ('fetch', 0.05), ('fetch', 0.05)], 20)

        self.assertEqual(
            line, 'Listing mode=discover_hub hub_id=trending: 500 ms, '
                  '20 items (import 200, fetch 100, other 200)')

    def test_report_is_silent_unless_the_service_enabled_it(self):
        self.timer.start()
        self.timer.mark('import')
        self.assertIsNone(self.timer.report('mode=hub'))
        self.assertEqual(self.logged, [])

        self.settings['listingTimingLog'] = 'true'
        self.timer.sync_window_property()
        line = self.timer.report('mode=hub')

        self.assertTrue(line.startswith('Listing mode=hub: '))
        self.assertEqual(self.logged, [line])

        self.settings['listingTimingLog'] = 'false'
        self.timer.sync_window_property()
        self.assertEqual(self.window, {})

    def test_mark_before_start_does_nothing(self):
        self.timer._STATE['start'] = None
        self.timer.mark('import')
        self.assertEqual(self.timer._PHASES, [])

    def test_label_keeps_the_identifying_params(self):
        self.assertEqual(
            self.timer.label({'mode': 'browseplex', 'key': '/hubs/x',
                              'section_id': '2', 'reload': '123'}),
            'mode=browseplex key=/hubs/x section_id=2')


class LazyRequestsTests(unittest.TestCase):
    def test_downloadutils_imports_requests_on_first_use_only(self):
        code = textwrap.dedent('''
            import sys, types
            sys.path.insert(0, %r)
            import resources.lib as lib
            for name in ('utils', 'clientinfo', 'app'):
                module = types.ModuleType('resources.lib.' + name)
                sys.modules['resources.lib.' + name] = module
                setattr(lib, name, module)
            from resources.lib import downloadutils
            from resources.lib.downloadutils import DownloadUtils
            print('requests' in sys.modules)
            print(downloadutils.exceptions.RequestException.__name__)
            print('requests' in sys.modules)
        ''' % str(REPO_ROOT))
        result = subprocess.run([sys.executable, '-c', code],
                                capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(),
                         ['False', 'RequestException', 'True'])


if __name__ == '__main__':
    unittest.main()
