#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Up Next integration for PlexKodiConnect
Sends episode information to the Up Next add-on so it can display
"next episode" notifications during playback.

See https://github.com/im85288/service.upnext/wiki/Integration
"""
from logging import getLogger
from json import dumps
from base64 import b64encode
from uuid import uuid4

import xbmc
import xbmcaddon

from . import variables as v
from . import plex_functions as PF
from . import utils
from .plex_api import API
from .plex_db import PlexDB
from .kodi_db import KodiVideoDB

LOG = getLogger('PLEX.upnext')
UPNEXT_ADDON_ID = 'service.upnext'
DEFAULT_CREDIT_COUNTDOWN_SECONDS = 10
MIN_CREDIT_COUNTDOWN_SECONDS = 5
MAX_CREDIT_COUNTDOWN_SECONDS = 30


def _upnext_addon():
    """Return a fresh Up Next settings handle so live changes are honored."""
    return xbmcaddon.Addon(id=UPNEXT_ADDON_ID)


def credit_timing_enabled():
    """Whether Up Next may replace PKC's credits control for this playback."""
    if utils.settings('useUpNextForEpisodeCredits') != 'true':
        return False
    if not xbmc.getCondVisibility('System.AddonIsEnabled(service.upnext)'):
        return False
    try:
        return _upnext_addon().getSetting('disableNextUp') != 'true'
    except Exception as err:  # Kodi raises RuntimeError if the add-on vanished
        LOG.warning('Could not inspect Up Next settings: %s', err)
        return False


def credit_countdown_seconds():
    """Return the bounded countdown used after a Plex credits marker."""
    try:
        countdown = int(utils.settings('upNextEpisodeCreditsCountdown'))
    except (TypeError, ValueError):
        countdown = DEFAULT_CREDIT_COUNTDOWN_SECONDS
    return min(MAX_CREDIT_COUNTDOWN_SECONDS,
               max(MIN_CREDIT_COUNTDOWN_SECONDS, countdown))


def _get_art_from_api(api):
    """
    Returns a dict with artwork URLs for Up Next from a plex API object.
    """
    art = {
        'thumb': '',
        'tvshow.clearart': '',
        'tvshow.clearlogo': '',
        'tvshow.fanart': '',
        'tvshow.landscape': '',
        'tvshow.poster': '',
    }
    # Get episode thumb
    thumb = api.one_artwork('thumb')
    if thumb:
        art['thumb'] = thumb
    # Get show artwork
    grandparent_thumb = api.one_artwork('grandparentThumb')
    if grandparent_thumb:
        art['tvshow.poster'] = grandparent_thumb
    grandparent_art = api.one_artwork('grandparentArt')
    if grandparent_art:
        art['tvshow.fanart'] = grandparent_art
    return art


def _episode_info(api):
    """
    Returns a dict with episode information for Up Next from a plex API object.
    """
    return {
        'episodeid': api.plex_id,
        'tvshowid': api.grandparent_id(),
        'title': api.title() or '',
        'art': _get_art_from_api(api),
        'season': api.season_number() or 0,
        'episode': api.index() or 0,
        'showtitle': api.grandparent_title() or '',
        'plot': api.plot() or '',
        'playcount': api.viewcount() or 0,
        'rating': api.rating() or 0,
        'firstaired': api.premiere_date() or '',
        'runtime': api.runtime() or 0,
    }


# Sentinel: a targeted lookup could not answer reliably; try the next one.
_UNKNOWN = object()
# Sentinel: the plex_id order and the season/episode order of the show
# disagree, so only the show's allLeaves response is authoritative.
_AMBIGUOUS = object()


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _strictly_increasing(values):
    return all(earlier < later for earlier, later in zip(values, values[1:]))


def _is_contiguous(current_number, next_number):
    """
    Whether next_number (season, episode) directly follows current_number,
    i.e. no episode can be missing from PKC's synced databases in between.
    Specials (season 0) are never considered contiguous.
    """
    season, episode = current_number
    next_season, next_episode = next_number
    if not season or episode is None:
        return False
    return (next_season == season and next_episode == episode + 1) or \
        (next_season == season + 1 and next_episode == 1)


def _next_episode_from_local_db(current_api):
    """
    Find the next episode with PKC's synced databases and fetch only that item.

    PMS returns allLeaves in a single order we cannot observe locally, so only
    answer when the plex_id order and the season/episode order of the synced
    episodes agree on the successor. Anything ambiguous (including "no next
    episode", which a not-yet-synced new episode could contradict) is left to
    the network lookups. The same applies if the successor does not directly
    follow the current episode, as an episode in between might not be synced.
    """
    show_id = current_api.grandparent_id()
    current_id = current_api.plex_id
    if show_id is None or current_id is None:
        return _UNKNOWN
    with PlexDB(lock=False) as plexdb:
        current = plexdb.episode(current_id)
        if not current or current.get('grandparent_id') is None:
            return _UNKNOWN
        plex_by_kodi = {
            episode['kodi_id']: episode['plex_id']
            for episode in plexdb.episode_by_show(show_id)
            if episode and episode['kodi_id'] is not None
        }
    with KodiVideoDB(lock=False) as kodidb:
        rows = kodidb.episode_numbers_by_show(current['grandparent_id'])
    if not rows or len(rows) != len(plex_by_kodi):
        return _UNKNOWN
    numbered = []
    for kodi_id, season, episode in rows:
        plex_id = plex_by_kodi.get(kodi_id)
        season, episode = _as_int(season), _as_int(episode)
        if plex_id is None or season is None or episode is None:
            return _UNKNOWN
        numbered.append(((season, episode), int(plex_id)))
    by_number = sorted(numbered)
    by_id = sorted(numbered, key=lambda entry: entry[1])
    if not _strictly_increasing([entry[0] for entry in by_number]) or \
            not _strictly_increasing([entry[1] for entry in by_id]):
        return _UNKNOWN
    current_number = (current_api.season_number(), current_api.index())
    try:
        number_pos = by_number.index((current_number, int(current_id)))
        id_pos = by_id.index((current_number, int(current_id)))
    except ValueError:
        # Current item missing or its numbering changed since the last sync
        return _UNKNOWN
    if number_pos + 1 >= len(by_number) or id_pos + 1 >= len(by_id):
        return _UNKNOWN
    if by_number[number_pos + 1] != by_id[id_pos + 1]:
        return _AMBIGUOUS
    next_number, next_id = by_number[number_pos + 1]
    if not _is_contiguous(current_number, next_number):
        return _UNKNOWN
    api = _fetch_episode(next_id, show_id)
    if api is _UNKNOWN or (api.season_number(), api.index()) != next_number:
        return _UNKNOWN
    return api


def _ordered_children(plex_id, exclude_all_leaves=False, check_ids=True):
    """
    Download a Plex item's children and return them only if their order is
    unambiguous (ascending by index and, with check_ids, by ratingKey alike),
    else None.
    """
    xml = PF.metadata_children(plex_id, exclude_all_leaves)
    try:
        children = list(xml)
    except TypeError:
        return None
    indices = [_as_int(child.get('index')) for child in children]
    ids = [_as_int(child.get('ratingKey')) for child in children]
    if None in indices or None in ids or \
            not _strictly_increasing(indices) or \
            (check_ids and not _strictly_increasing(ids)):
        return None
    return children


def _fetch_episode(plex_id, show_id):
    """Fetch one episode's full metadata, as allLeaves would have listed it."""
    xml = PF.GetPlexMetadata(plex_id)
    try:
        api = API(xml[0])
    except (TypeError, IndexError, AttributeError):
        return _UNKNOWN
    if api.plex_id != plex_id or api.grandparent_id() != show_id:
        return _UNKNOWN
    return api


def _next_episode_from_seasons(current_api):
    """
    Find the next episode from the current season's children, then the
    following seasons', instead of downloading the entire show.
    """
    season_id = current_api.season_id()
    show_id = current_api.grandparent_id()
    current_id = current_api.plex_id
    if season_id is None or show_id is None or current_id is None:
        return _UNKNOWN
    episodes = _ordered_children(season_id)
    if episodes is None:
        return _UNKNOWN
    ids = [_as_int(episode.get('ratingKey')) for episode in episodes]
    try:
        position = ids.index(current_id)
    except ValueError:
        return _UNKNOWN
    if position + 1 < len(episodes):
        return _fetch_episode(ids[position + 1], show_id)
    seasons = _ordered_children(
        show_id, exclude_all_leaves=True, check_ids=False)
    if seasons is None:
        return _UNKNOWN
    season_ids = [_as_int(season.get('ratingKey')) for season in seasons]
    try:
        season_position = season_ids.index(season_id)
    except ValueError:
        return _UNKNOWN
    for next_season_id in season_ids[season_position + 1:]:
        next_episodes = _ordered_children(next_season_id)
        if next_episodes is None:
            return _UNKNOWN
        if next_episodes:
            next_id = _as_int(next_episodes[0].get('ratingKey'))
            if next_id <= current_id:
                # Season order and plex_id order disagree
                return _UNKNOWN
            return _fetch_episode(next_id, show_id)
    LOG.debug('No next episode - this was the last one')
    return None


def _next_episode_from_all_leaves(current_api):
    """Original lookup: download every episode of the show."""
    xml = PF.show_episodes(current_api.grandparent_id())
    if xml is None:
        return None
    for counter, episode in enumerate(xml):
        api = API(episode)
        if api.plex_id == current_api.plex_id:
            break
    else:
        LOG.debug('Did not find the episode with Plex id %s for show %s: %s',
                  current_api.plex_id, current_api.grandparent_id(),
                  current_api.grandparent_title())
        return None
    try:
        return API(xml[counter + 1])
    except IndexError:
        # Was the last episode
        LOG.debug('No next episode - this was the last one')
        return None


def _get_next_episode_api(current_api):
    """
    Returns the API object for the next episode after the current one.
    Returns None if something went wrong or there is no next episode.

    Tries the synced databases plus one metadata request first, then the
    current/next season's children, and only then the show's allLeaves.
    """
    for lookup in (_next_episode_from_local_db, _next_episode_from_seasons):
        try:
            result = lookup(current_api)
        except Exception:
            LOG.exception('Up Next lookup %s failed', lookup.__name__)
            continue
        if result is _AMBIGUOUS:
            break
        if result is not _UNKNOWN:
            return result
    return _next_episode_from_all_leaves(current_api)


def _upnext_signal(data):
    """
    Sends the Up Next signal via JSON RPC.
    This is the recommended way according to Up Next documentation.
    """
    sender = '%s.SIGNAL' % v.ADDON_ID
    encoded_data = b64encode(dumps(data).encode('utf-8')).decode('ascii')
    params = {
        'sender': sender,
        'message': 'upnext_data',
        'data': [encoded_data],
    }
    result = xbmc.executeJSONRPC(dumps({
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'JSONRPC.NotifyAll',
        'params': params,
    }))
    LOG.debug('Up Next signal sent. Result: %s', result)


def prepare_upnext_signal(current_api, notification_time=None):
    """
    Prepare the Up Next signal if there is a next episode.

    Args:
        current_api: The API object of the currently playing episode
        notification_time: Optional time in seconds before the end to show
                          the notification. If None, Up Next uses its default.

    Returns:
        Handoff token, next Plex id, and payload if available, False otherwise
    """
    if current_api.plex_type != v.PLEX_TYPE_EPISODE:
        LOG.debug('Not an episode - skipping Up Next signal')
        return False

    next_api = _get_next_episode_api(current_api)
    if next_api is None:
        LOG.debug('No next episode available for Up Next')
        return False

    LOG.debug('Preparing Up Next signal for episode "%s" -> "%s"',
              current_api.title(), next_api.title())

    # The one-time token proves that a later playback request came from this
    # exact Up Next signal rather than an unrelated library selection.
    handoff_token = uuid4().hex
    # This URL will be called by Up Next to start playback.
    play_url = ('plugin://%s?plex_id=%s&plex_type=%s&mode=play&'
                'pkc_upnext=%s') % (
                    v.ADDON_ID,
                    next_api.plex_id,
                    v.PLEX_TYPE_EPISODE,
                    handoff_token,
                )

    # Build the data structure for Up Next
    upnext_data = {
        'current_episode': _episode_info(current_api),
        'next_episode': _episode_info(next_api),
        'play_url': play_url,
    }

    # Marker-aware Up Next versions use this bounded duration to count down
    # from the credits marker instead of keeping the dialog open until EOF.
    if notification_time is not None:
        upnext_data['notification_time'] = notification_time
        upnext_data['notification_duration'] = credit_countdown_seconds()

    LOG.debug('Sending Up Next data: current="%s" S%02dE%02d, next="%s" S%02dE%02d',
              current_api.grandparent_title(),
              current_api.season_number() or 0,
              current_api.index() or 0,
              next_api.grandparent_title(),
              next_api.season_number() or 0,
              next_api.index() or 0)

    return {
        'token': handoff_token,
        'next_plex_id': next_api.plex_id,
        'data': upnext_data,
    }


def emit_upnext_signal(prepared):
    """Emit a prepared Up Next payload and return its handoff identity."""
    _upnext_signal(prepared['data'])
    return {
        'token': prepared['token'],
        'next_plex_id': prepared['next_plex_id'],
    }


def send_upnext_signal(current_api, notification_time=None):
    """Prepare and immediately emit an Up Next signal."""
    prepared = prepare_upnext_signal(current_api, notification_time)
    if not prepared:
        return False
    return emit_upnext_signal(prepared)


def _get_total_seconds_from_kodi_time(total_time):
    """
    Convert Kodi time dict to total seconds.

    Args:
        total_time: Dict with 'hours', 'minutes', 'seconds' keys

    Returns:
        Total seconds as int, or 0 if total_time is None
    """
    if not total_time:
        return 0
    return (total_time.get('hours', 0) * 3600 +
            total_time.get('minutes', 0) * 60 +
            total_time.get('seconds', 0) +
            total_time.get('milliseconds', 0) / 1000.0)


def _calculate_notification_time(marker, total_seconds, marker_name):
    """
    Calculate notification time from a credits marker.

    Args:
        marker: Tuple (start_time, end_time) in seconds
        total_seconds: Total duration in seconds
        marker_name: Name of the marker for logging

    Returns:
        Notification time in seconds before end, or None if invalid
    """
    try:
        marker_start = float(marker[0])
        total_seconds = float(total_seconds)
    except (IndexError, TypeError, ValueError):
        return None
    if total_seconds > 0 and 0 <= marker_start < total_seconds:
        marker_remaining = total_seconds - marker_start
        LOG.info('Using %s for Up Next: marker at %.3fs, showing at '
                 '%.3fs before end with a %ss countdown', marker_name,
                 marker_start, marker_remaining,
                 credit_countdown_seconds())
        return marker_remaining
    return None


def get_notification_time_from_markers(status):
    """
    Get the notification time from Plex credits markers if available.

    Args:
        status: The player status dict containing markers info

    Returns:
        The notification time in seconds before the end, or None if not available
    """
    if not credit_timing_enabled():
        return None
    total_seconds = _get_total_seconds_from_kodi_time(status.get('totaltime'))

    # First check for first credits marker (intro to credits)
    first_credits = status.get('first_credits_marker')
    notification_time = _calculate_notification_time(
        first_credits, total_seconds, 'first credits marker')
    if notification_time is not None:
        return notification_time

    # Fall back to final credits marker
    final_credits = status.get('final_credits_marker')
    return _calculate_notification_time(
        final_credits, total_seconds, 'final credits marker')
