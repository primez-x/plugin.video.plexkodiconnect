"""Native Plex catalogue routes for the Starlight skin.

Plex JSON becomes PKC metadata before PKC resolves native library identities.
Kodi imports remain local so catalogue transforms can run without Kodi.
"""
from datetime import date
import re
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import uuid4
import xml.etree.ElementTree as ET

PREFIX = 'plugin://plugin.video.plexkodiconnect/starlight/'
DISCOVER = 'https://discover.provider.plex.tv'
METADATA = 'https://metadata.provider.plex.tv'
HOSTS = ('discover.provider.plex.tv', 'metadata.provider.plex.tv')
ROUTES = {'discover', 'genres', 'search', 'people', 'person', 'credits',
          'images', 'item', 'cast', 'watchlist', 'trailers', 'trailer', 'artwork'}
HUBS = {'trending': 'top_watchlisted', 'popular': 'trending-plex',
        'upcoming': 'coming-soon', 'movies': 'top_watchlisted',
        'tvshows': 'top_watchlisted', 'top_rated': 'top_watchlisted'}
KEY = re.compile(r'[A-Za-z0-9_-]{1,128}')
PERSON_KEY = re.compile(r'[0-9a-f]{24}')
SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,80}')


def route(name, **params):
    return PREFIX + name + ('?' + urlencode(params) if params else '')


def parse_route(path, params):
    parsed = urlsplit(path)
    name = parsed.path[len('/starlight/'):]
    if (parsed.scheme != 'plugin' or parsed.netloc != 'plugin.video.plexkodiconnect'
            or parsed.path != '/starlight/' + name or name not in ROUTES):
        raise ValueError('Unknown Starlight route')
    params = dict(params)
    if name in ('item', 'watchlist', 'trailers', 'trailer', 'artwork'):
        if not KEY.fullmatch(params.get('rating_key', '')):
            raise ValueError('Invalid Plex rating key')
    if name == 'watchlist' and params.get('action') not in ('add', 'remove'):
        raise ValueError('Invalid Watchlist action')
    if name == 'watchlist' and params.get('type', 'movie') not in ('movie', 'show'):
        raise ValueError('Invalid Watchlist media type')
    if name in ('person', 'credits', 'images'):
        key = params.get('person_key', '')
        if (key and not PERSON_KEY.fullmatch(key)) or (not key and (name != 'person' or not params.get('name', '').strip())):
            raise ValueError('Invalid Plex person key')
    if name == 'discover':
        if params.get('hub', 'trending') not in HUBS:
            raise ValueError('Unknown Discover hub')
        if params.get('genre') and not SLUG.fullmatch(params['genre']):
            raise ValueError('Invalid genre')
    if name == 'cast':
        if not KEY.fullmatch(params.get('rating_key', '')):
            if (not params.get('dbid', '').isdigit()
                    or int(params['dbid']) < 1
                    or params.get('type') not in ('movie', 'tvshow', 'episode')):
                raise ValueError('Invalid cast identity')
    params['query'] = params.get('query', '').strip()[:200]
    return name, params


def safe_image(value):
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or any(c in value for c in ('|', '"', '\r', '\n')) or parsed.fragment):
            return ''
        if any(k.lower() in ('x-plex-token', 'token', 'auth_token', 'apikey', 'api_key')
               for k, _ in parse_qsl(parsed.query)):
            return ''
    except ValueError:
        return ''
    return value


def artwork(record):
    art = {}
    for key, field in (('poster', 'thumb'), ('thumb', 'thumb'), ('fanart', 'art'),
                       ('landscape', 'art'), ('clearlogo', 'clearLogo')):
        value = safe_image(record.get(field))
        if value:
            art[key] = value
    mapping = {'background': 'fanart', 'coverArt': 'landscape',
               'coverPoster': 'poster', 'clearLogo': 'clearlogo', 'snapshot': 'thumb'}
    for image in record.get('Image', []):
        value = safe_image(image.get('url'))
        key = mapping.get(image.get('type'))
        if key and value:
            art[key] = value
    return art


def _element(name, record):
    element = ET.Element(name)
    for key, value in record.items():
        if key.startswith('_') or value is None:
            continue
        if isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    element.append(_element(key, child))
        elif isinstance(value, dict):
            element.append(_element(key, value))
        else:
            element.set(key, str(int(value)) if isinstance(value, bool) else str(value))
    return element


def metadata(records):
    result = []
    seen = set()
    for record in records:
        key = str(record.get('ratingKey', ''))
        kind = record.get('type')
        if kind not in ('movie', 'show') or not KEY.fullmatch(key) or key in seen:
            continue
        seen.add(key)
        record = dict(record)
        record.setdefault('guid', 'plex://%s/%s' % (kind, key))
        result.append(_element('Video' if kind == 'movie' else 'Directory', record))
    return result


def search_records(container):
    titles, people = [], []
    for group in container.get('SearchResults', []):
        for result in group.get('SearchResult', []):
            record = result.get('Metadata') or result.get('Directory') or {}
            if record.get('type') in ('movie', 'show'):
                titles.append(record)
            elif record.get('type') == 'person':
                people.append(record)
    return titles, people


def credits(container):
    records = {}
    for group in container.get('CreditGroup', []):
        for credit in group.get('Credit', []):
            record = dict(credit.get('Metadata') or {})
            key = record.get('ratingKey')
            if not key or record.get('type') not in ('movie', 'show'):
                continue
            role = credit.get('role') or group.get('title') or group.get('type', '')
            if key in records:
                roles = records[key]['_role'].split(' / ')
                if role not in roles:
                    records[key]['_role'] += ' / ' + role
            else:
                record['_role'] = role
                record.setdefault('guid', 'plex://%s/%s' % (record['type'], key))
                records[key] = record
    return sorted(records.values(), key=lambda r: (
        -int(r.get('year') or 0), r.get('title', '').casefold(), r['ratingKey']))


def hydrate(records):
    """Fetch external identities in batches; Discover ignores includeGuids."""
    wanted = [r['ratingKey'] for r in records if r.get('type') in ('movie', 'show')
              and KEY.fullmatch(str(r.get('ratingKey', ''))) and not r.get('Guid')]
    details = {}
    for start in range(0, len(wanted), 20):
        url = METADATA + '/library/metadata/' + ','.join(wanted[start:start + 20])
        for record in _fetch(url + '?includeGuids=1&X-Plex-Container-Size=20').get('Metadata', []):
            details[record.get('ratingKey')] = record
    return [dict(record, **{k: v for k, v in details.get(record.get('ratingKey'), {}).items()
                            if k != 'userState' or k not in record}) for record in records]


def person_properties(person):
    key = person.get('ratingKey') or person.get('metadataId', '')
    if not PERSON_KEY.fullmatch(key):
        return {}
    parts = [person.get('knownFor') or next((c.get('title', '') for c in person.get('CreditType', [])), '')]
    born = person.get('bornAt', '')
    if born:
        try:
            birth = date.fromisoformat(born)
            end = date.fromisoformat(person['diedAt']) if person.get('diedAt') else date.today()
            age = end.year - birth.year - ((end.month, end.day) < (birth.month, birth.day))
            parts.append('%s (%d)' % (born, age))
        except (TypeError, ValueError):
            parts.append(born)
    parts.append(person.get('birthPlace', ''))
    return {'Name': person.get('title', ''), 'Thumb': safe_image(person.get('thumb')),
            'Meta': ' · '.join(str(p) for p in parts if p),
            'Biography': re.sub(r'<[^>]*>', '', person.get('summary', '')),
            'ProviderKey': key, 'CreditsPath': route('credits', person_key=key),
            'ImagesPath': route('images', person_key=key)}


def publish_person(window, request, fields):
    for key, value in fields.items():
        if window.getProperty('Starlight.Person.Request') != request:
            return False
        window.setProperty('Starlight.Person.' + key, value)
    return True


def person_images(container, person):
    images = container.get('Image', []) or person.get('Image', [])
    result, seen = [], set()
    for image in images:
        url = safe_image(image.get('url'))
        if url and url not in seen:
            seen.add(url)
            result.append(dict(image, url=url))
    return result


def smarttube_trailer(title, year):
    return 'plugin://plugin.video.smarttube/trailer?' + urlencode({'title': title, 'year': year or ''})


def play_url(url):
    import sys
    import xbmc
    import xbmcgui
    import xbmcplugin
    if int(sys.argv[1]) >= 0:
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, xbmcgui.ListItem(path=url))
    else:
        xbmc.Player().play(url)


def watchlist_request(key, action, kind='movie'):
    if not KEY.fullmatch(key) or action not in ('add', 'remove') or kind not in ('movie', 'show'):
        raise ValueError('Invalid Watchlist request')
    return {'rating_key': key, 'plex_type': kind}


def _fetch(url):
    from . import clientinfo, discover_cache, entrypoint, plex_discover, utils
    from .downloadutils import DownloadUtils
    account = discover_cache.active_account_hash()
    if not account or not utils.window('plex_token') or utils.window('plex_restricteduser') == 'true':
        raise entrypoint.ListingException()
    headers = clientinfo.getXArgsDeviceInfo(
        {'X-Plex-Token': utils.window('plex_token'), 'Accept': 'application/json'}, include_token=False)
    response = DownloadUtils().downloadUrl(url, authenticate=False, headerOptions=headers,
                                          return_response=True, allow_redirects=False,
                                          timeout=entrypoint.DISCOVER_HTTP_TIMEOUT)
    if response is not None and response.status_code in entrypoint.DISCOVER_REDIRECT_STATUS_CODES:
        redirects = plex_discover.provider_redirect_urls(response.headers.get('Location'), HOSTS)
        if redirects:
            response = DownloadUtils().downloadUrl(redirects[0], authenticate=False, headerOptions=headers,
                                                  return_response=True, allow_redirects=False,
                                                  timeout=entrypoint.DISCOVER_HTTP_TIMEOUT)
    if (response is None or response.status_code != 200
            or not discover_cache.account_matches(account)):
        raise entrypoint.ListingException()
    try:
        payload = response.json().get('MediaContainer')
    except (ValueError, AttributeError):
        raise entrypoint.ListingException()
    if not isinstance(payload, dict):
        raise entrypoint.ListingException()
    return payload


def _pages(url):
    from . import discover_cache, entrypoint
    account = discover_cache.active_account_hash()
    records, start = [], 0
    while True:
        page = _fetch(url + ('&' if '?' in url else '?') + urlencode({
            'X-Plex-Container-Start': start, 'X-Plex-Container-Size': 100}))
        if not discover_cache.account_matches(account):
            raise entrypoint.ListingException()
        batch = page.get('Metadata', [])
        records.extend(batch)
        start += len(batch)
        total = int(page.get('totalSize', start))
        if start >= total:
            return records
        if not batch:
            from .entrypoint import ListingException
            raise ListingException()


def build_cards(records, source, online=False, account_hash=None):
    from . import discover_cache, entrypoint, watchlist
    by_key = {str(r.get('ratingKey')): r for r in records}
    cards = []
    for _, item, folder in entrypoint._discover_cards(metadata(records), source, account_hash=account_hash):
        props = item.setdefault('extraproperties', {})
        key = props['ratingKey']
        record = by_key[key]
        local = bool(item.get('movieid') or item.get('tvshowid') or props.get('DBID'))
        if online and local:
            continue
        hint = discover_cache.watchlist_hint(key, watchlist.provider_state_hint(record.get('userState')))
        if hint:
            props['PlexWatchlistHint'] = hint
        item.setdefault('art', {}).update(artwork(record))
        props.update({'Starlight.Source': 'More from Plex' if online else 'Plex',
                      'Starlight.ProviderKey': key, 'Starlight.InLibrary': str(local).lower(),
                      'Starlight.InfoPath': route('item', rating_key=key),
                      'Starlight.CastPath': route('cast', rating_key=key),
                      'Starlight.TrailersPath': route('trailers', rating_key=key),
                      'Starlight.WatchlistAdd': route('watchlist', rating_key=key, action='add', type=record['type']),
                      'Starlight.WatchlistRemove': route('watchlist', rating_key=key, action='remove', type=record['type']),
                      'Starlight.RoleYear': ' · '.join(str(v) for v in (record.get('_role'), record.get('year')) if v)})
        present = props.get('PlexWatchlistHint') == 'present'
        props['Starlight.PrimaryLabel'] = ('Play' if local else 'Remove from watchlist' if present else 'Add to watchlist')
        props['Starlight.PrimaryAction'] = item['file'] if local else props['Starlight.WatchlistRemove' if present else 'Starlight.WatchlistAdd']
        props['Starlight.BoardRank'] = str(len(cards) + 1)
        if not local:
            item['file'] = props['Starlight.InfoPath']
            item['isFolder'] = False
            item['IsPlayable'] = 'false'
        item['trailer'] = route('trailer', rating_key=key)
        cards.append((item['file'], item, folder if local else False))
    return cards


def _emit(cards, content='movies'):
    import sys
    import xbmcplugin
    from . import widgets
    output = [widgets.create_listitem(widgets.prepare_listitem(card)) for _, card, _ in cards]
    output = [card for card in output if card is not None]
    xbmcplugin.setContent(int(sys.argv[1]), content)
    xbmcplugin.addSortMethod(int(sys.argv[1]), xbmcplugin.SORT_METHOD_UNSORTED)
    xbmcplugin.addDirectoryItems(int(sys.argv[1]), output, len(output))


def _person_cards(people):
    import sys
    import xbmcgui
    import xbmcplugin
    cards = []
    for person in people:
        key = person.get('metadataId') or person.get('ratingKey') or person.get('key', '').rsplit('/', 1)[-1]
        name = person.get('title') or person.get('tag', '')
        url = route('person', person_key=key, name=name) if PERSON_KEY.fullmatch(str(key)) else route('person', name=name)
        card = xbmcgui.ListItem(name, path=url, offscreen=True)
        card.setArt({'thumb': safe_image(person.get('thumb'))})
        card.getVideoInfoTag().setTitle(name)
        card.setProperty('Starlight.PersonKey', str(key) if PERSON_KEY.fullmatch(str(key)) else '')
        card.setProperty('Starlight.RoleYear', person.get('role', ''))
        card.setProperty('Starlight.Source', 'Plex')
        card.setProperty('IsPlayable', 'false')
        cards.append((url, card, False))
    xbmcplugin.setContent(int(sys.argv[1]), 'actors')
    xbmcplugin.addDirectoryItems(int(sys.argv[1]), cards, len(cards))


def _person(params):
    import xbmc
    import xbmcgui
    from . import discover_cache
    account = discover_cache.active_account_hash()
    window = xbmcgui.Window(11103)
    request = params.get('request')
    if request:
        if window.getProperty('Starlight.Person.Request') != request:
            return
    else:
        request = uuid4().hex
        window.setProperty('Starlight.Person.Request', request)
        publish_person(window, request, {'Name': params.get('name', ''), 'Thumb': '', 'Meta': '',
                                       'Tab': 'library', 'Hub': xbmcgui.Window(10000).getProperty('Starlight.Hub')})
    publish_person(window, request, dict.fromkeys(('Biography', 'ProviderKey', 'CreditsPath', 'ImagesPath'), ''))
    key = params.get('person_key')
    if not key:
        _, people = search_records(_fetch(DISCOVER + '/library/search?' + urlencode({
            'query': params['name'], 'searchTypes': 'people', 'searchProviders': 'discover', 'includeMetadata': 1})))
        matches = [p for p in people if p.get('title', '').casefold() == params['name'].strip().casefold()]
        if len(matches) != 1:
            xbmc.executebuiltin('ActivateWindow(11103)')
            return
        key = matches[0].get('metadataId')
        if not PERSON_KEY.fullmatch(str(key)):
            return
    people = _fetch(DISCOVER + '/library/people/' + key).get('Metadata', [])
    if people and discover_cache.account_matches(account):
        publish_person(window, request, person_properties(people[0]))
    if (window.getProperty('Starlight.Person.Request') == request
            and discover_cache.account_matches(account)):
        xbmc.executebuiltin('ActivateWindow(11103)')


def _image_cards(images):
    import sys
    import xbmcgui
    import xbmcplugin
    cards = []
    for image in images:
        url = image['url']
        card = xbmcgui.ListItem(image.get('alt') or image.get('type', 'Image'), path=url, offscreen=True)
        card.setArt({'thumb': url})
        card.getVideoInfoTag().setTitle(image.get('alt') or image.get('type', 'Image'))
        card.setProperty('Starlight.Source', 'Plex')
        cards.append((url, card, False))
    xbmcplugin.setContent(int(sys.argv[1]), 'images')
    xbmcplugin.addDirectoryItems(int(sys.argv[1]), cards, len(cards))


def dispatch(path, params):
    """Run one validated route and close directory handles exactly once."""
    import sys
    import xbmcgui
    import xbmcplugin
    from . import app, discover_cache, entrypoint, transfer, watchlist, widgets
    try:
        name, params = parse_route(path, params)
    except ValueError:
        if int(sys.argv[1]) >= 0:
            xbmcplugin.endOfDirectory(int(sys.argv[1]), False)
        return
    app.init(entrypoint=True)
    directory = name not in ('item', 'person', 'watchlist', 'trailer')
    account = discover_cache.active_account_hash()
    try:
        if name == 'watchlist':
            args = watchlist_request(params['rating_key'], params['action'], params.get('type', 'movie'))
            request = watchlist.begin_key(args, 'present' if params['action'] == 'add' else 'absent')
            if request is not None:
                transfer.plex_command('WATCHLIST_DETAIL_KEY?' + urlencode(request))
        elif name == 'person':
            _person(params)
        elif name == 'discover':
            hub = params.get('hub', 'trending')
            genre = params.get('genre')
            url = (DISCOVER + '/library/categories/' + genre + '/' + {
                'trending': 'trending-' + genre, 'popular': 'genre-popular',
                'upcoming': 'coming-soon'}.get(hub, 'top-watchlisted')) if genre else (
                    DISCOVER + '/hubs/sections/home/' + HUBS[hub])
            records = _pages(url)
            if hub in ('movies', 'tvshows'):
                records = [r for r in records if r.get('type') == ('movie' if hub == 'movies' else 'show')]
            if hub == 'top_rated':
                records.sort(key=lambda r: -float(r.get('rating') or r.get('audienceRating') or 0))
            _emit(build_cards(hydrate(records), url, account_hash=account))
        elif name == 'genres':
            container = _fetch(DISCOVER + '/hubs/sections/home/genres')
            cards = []
            for genre in container.get('Directory', []):
                slug = genre.get('slug', '')
                if SLUG.fullmatch(slug):
                    url = route('discover', genre=slug, hub='trending')
                    title = genre.get('title', slug)
                    card = xbmcgui.ListItem(title, path=url, offscreen=True)
                    card.setArt({'thumb': safe_image(genre.get('thumb'))})
                    card.getVideoInfoTag().setTitle(title)
                    card.setProperty('Starlight.Source', 'Plex')
                    cards.append((url, card, True))
            xbmcplugin.setContent(int(sys.argv[1]), 'files')
            xbmcplugin.addDirectoryItems(int(sys.argv[1]), cards, len(cards))
        elif name in ('search', 'people'):
            if params['query']:
                url = DISCOVER + '/library/search?' + urlencode({'query': params['query'], 'searchProviders': 'discover',
                                                               'searchTypes': 'people' if name == 'people' else 'movies,tv',
                                                               'includeMetadata': 1, 'limit': 50})
                titles, people = search_records(_fetch(url))
                if name == 'people':
                    _person_cards(people)
                else:
                    xbmcplugin.setPluginCategory(int(sys.argv[1]), 'More from Plex')
                    _emit(build_cards(hydrate(titles), url, online=True, account_hash=account))
        elif name == 'credits':
            url = DISCOVER + '/library/people/' + params['person_key'] + '/credits'
            _emit(build_cards(hydrate(credits(_fetch(url))), url, account_hash=account))
        elif name == 'images':
            url = DISCOVER + '/library/people/' + params['person_key']
            person = (_fetch(url).get('Metadata') or [{}])[0]
            _image_cards(person_images(_fetch(url + '/images'), person))
        elif name == 'cast' and params.get('dbid'):
            from .plex_db import PlexDB
            from . import plex_functions
            with PlexDB(lock=False) as database:
                local = database.item_by_kodi_id(int(params['dbid']), params['type'])
            if local:
                root = plex_functions.GetPlexMetadata(local['plex_id'])
                if ET.iselement(root) and len(root):
                    _person_cards([dict(r.attrib) for r in root[0].findall('Role')])
        elif name == 'cast':
            record = (_fetch(METADATA + '/library/metadata/' + params['rating_key']).get('Metadata') or [{}])[0]
            _person_cards(record.get('Role', []))
        elif name in ('trailers', 'trailer'):
            _trailers(params['rating_key'], play=name == 'trailer')
        else:
            url = METADATA + '/library/metadata/' + params['rating_key'] + '?includeGuids=1'
            records = _fetch(url).get('Metadata', [])
            if name == 'artwork':
                _image_cards([{'type': key, 'url': value} for r in records for key, value in artwork(r).items()])
            else:
                cards = build_cards(records, url, account_hash=account)
                if cards:
                    card = widgets.create_listitem(widgets.prepare_listitem(cards[0][1]), as_tuple=False)
                    if discover_cache.account_matches(account):
                        xbmcgui.Dialog().info(card)
    except entrypoint.ListingException:
        if directory:
            xbmcplugin.endOfDirectory(int(sys.argv[1]), False)
        else:
            if name == 'trailer' and int(sys.argv[1]) >= 0:
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
            xbmcgui.Dialog().notification('Plex', 'Plex metadata is unavailable')
        return
    if directory:
        xbmcplugin.endOfDirectory(int(sys.argv[1]), discover_cache.account_matches(account), cacheToDisc=False)


def _trailers(key, play=False):
    import sys
    import xbmcgui
    import xbmcplugin
    from . import discover_cache, entrypoint, plex_functions
    from .plex_api import mass_api
    account = discover_cache.active_account_hash()
    record = (_fetch(METADATA + '/library/metadata/' + key + '?includeGuids=1').get('Metadata') or [{}])[0]
    # Prefer the exact synced PMS title and PKC's existing local-extra player.
    matches = mass_api(metadata([record]), check_by_guid=True, match_external_ids=True)
    if matches:
        root = plex_functions.GetPlexMetadata(matches[0].plex_id)
        if ET.iselement(root) and len(root):
            api = entrypoint.API(root[0])
            trailer = api.trailer(force_play=True)
            if not play and api.extras() is not None:
                entrypoint.extras(matches[0].plex_id)
                return
            if trailer:
                if play:
                    if not discover_cache.account_matches(account):
                        raise entrypoint.ListingException()
                    play_url(trailer)
                else:
                    entrypoint.extras(matches[0].plex_id)
                return
    try:
        extras = _pages(METADATA + '/library/metadata/' + key + '/extras')
    except entrypoint.ListingException:
        extras = []
    choices = []
    for extra in sorted(extras, key=lambda r: r.get('extraType') != 1):
        for media in extra.get('Media', []):
            url = safe_image(media.get('url'))
            if url and urlsplit(url).hostname not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
                choices.append((url, extra))
                break
    if not choices:
        choices = [(smarttube_trailer(record.get('title', ''), record.get('year')), {'title': 'Search trailers on SmartTube'})]
    if play:
        if not discover_cache.account_matches(account):
            raise entrypoint.ListingException()
        play_url(choices[0][0])
        return
    cards = []
    for url, extra in choices:
        card = xbmcgui.ListItem(extra.get('title', 'Trailer'), path=url, offscreen=True)
        card.getVideoInfoTag().setTitle(extra.get('title', 'Trailer'))
        card.setArt(artwork(extra))
        card.setProperty('Starlight.Source', 'SmartTube' if url.startswith('plugin://plugin.video.smarttube/') else 'Plex')
        card.setProperty('IsPlayable', 'true')
        cards.append((url, card, False))
    xbmcplugin.setContent(int(sys.argv[1]), 'videos')
    xbmcplugin.addDirectoryItems(int(sys.argv[1]), cards, len(cards))
