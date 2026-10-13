#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Used to shovel data from separate Kodi Python instances to the main thread
and vice versa.
"""
from logging import getLogger
import json
from time import monotonic

import xbmc
import xbmcgui

LOG = getLogger('PLEX.transfer')
WINDOW = xbmcgui.Window(10000)
WINDOW_UPSTREAM = 'plexkodiconnect.result.upstream'
WINDOW_DOWNSTREAM = 'plexkodiconnect.result.downstream'
WINDOW_COMMAND = 'plexkodiconnect.command'
# JSONRPC.NotifyAll wake-up for the PKC service, see kodimonitor
SIGNAL_SENDER = 'plugin.video.plexkodiconnect.SIGNAL'
COMMAND_MESSAGE = 'pkc_command'
# Polling cadence for window-property hand-offs between Python instances
POLL_INTERVAL_MS = 20
# Upper bounds so a dead or busy service cannot hang a plugin instance
# forever. The transfer wait must outlast any playback dialog the service
# might show the user (resume/version pickers), hence generous.
COMMAND_TIMEOUT_SECONDS = 30.0
TRANSFER_TIMEOUT_SECONDS = 300.0
MONITOR = xbmc.Monitor()
KODIVERSION = int(xbmc.getInfoLabel("System.BuildVersion")[:2])


def cast(func, value):
    """
    Cast the specified value to the specified type (returned by func). Currently
    this only support int, float, bool. Should be extended if needed.
    Parameters:
        func (func): Calback function to used cast to type (int, bool, float).
        value (any): value to be cast and returned.

    Returns None if something goes wrong
    """
    if value is None:
        return value
    elif func == bool:
        return bool(int(value))
    elif func == str:
        if isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            return value
        else:
            return value.decode('utf-8')
    elif func == str:
        if isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            return value
        else:
            return value.encode('utf-8')
    elif func == int:
        try:
            return int(value)
        except ValueError:
            try:
                # Converting e.g. '8.0' fails; need to convert to float first
                return int(float(value))
            except ValueError:
                return
    elif func == float:
        try:
            return float(value)
        except ValueError:
            return
    return func(value)


def kodi_window(property, value=None, clear=False):
    """
    Get or set window property - thread safe! value must be string
    """
    if clear:
        WINDOW.clearProperty(property)
    elif value is not None:
        WINDOW.setProperty(property, value)
    else:
        return WINDOW.getProperty(property)


def _wait_poll(deadline):
    """Sleep one poll interval. Returns True while waiting should continue."""
    if MONITOR.waitForAbort(POLL_INTERVAL_MS / 1000.0):
        return False
    return monotonic() < deadline


def plex_command(value):
    """
    Used to funnel states between different Python instances. NOT really thread
    safe - let's hope the Kodi user can't click fast enough

    Returns True if the command was posted, False if the previous command
    was never picked up (service dead/busy) or Kodi is shutting down. A
    dropped command is logged instead of overwriting the pending one.
    """
    deadline = monotonic() + COMMAND_TIMEOUT_SECONDS
    while kodi_window(WINDOW_COMMAND):
        if not _wait_poll(deadline):
            LOG.error('Dropping command %r: previous command never picked '
                      'up or Kodi is shutting down', value)
            return False
    kodi_window(WINDOW_COMMAND, value=value)
    notify_service()
    return True


def notify_service():
    """
    Wake the PKC service main loop right away instead of letting it find the
    command on its next poll. The window property stays the source of truth;
    a lost notification only costs the service's regular polling delay.
    """
    try:
        xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0',
            'id': 'pkc-command-wake',
            'method': 'JSONRPC.NotifyAll',
            'params': {
                'sender': SIGNAL_SENDER,
                'message': COMMAND_MESSAGE,
                'data': [],
            },
        }))
        return True
    except Exception:
        return False


def serialize(obj):
    if isinstance(obj, PKCListItem):
        return {'type': 'PKCListItem', 'data': obj.data}
    else:
        return {'type': 'other', 'data': obj}
    return


def de_serialize(answ):
    if answ['type'] == 'PKCListItem':
        result = PKCListItem()
        result.data = answ['data']
        return convert_pkc_to_listitem(result)
    elif answ['type'] == 'other':
        return answ['data']
    else:
        raise NotImplementedError('Not implemented: %s' % answ)


def send(pkc_listitem, target='default'):
    """
    Pickles the obj to the window variable. Use to transfer Python
    objects between different PKC python instances (e.g. if default.py is
    called and you'd want to use the service.py instance)

    obj can be pretty much any Python object. However, classes and
    functions won't work. See the Pickle documentation

    Set target='default' if you send data TO another Python default.py
    instance, 'main' if your default.py needs to send to the main thread
    """
    window = WINDOW_DOWNSTREAM if target == 'default' else WINDOW_UPSTREAM
    LOG.debug('Sending: %s', pkc_listitem)
    kodi_window(window,
                value=json.dumps(serialize(pkc_listitem)))


def wait_for_transfer(source='main', timeout=TRANSFER_TIMEOUT_SECONDS):
    """
    Set source='default' if you wait for data FROM another Python default.py
    instance, 'main' if your default.py needs to wait for the main thread

    Returns None on timeout or when Kodi is shutting down; callers must
    handle that like a failed hand-off.
    """
    LOG.debug('Waiting for transfer from %s', source)
    window = WINDOW_DOWNSTREAM if source == 'main' else WINDOW_UPSTREAM
    deadline = monotonic() + timeout
    while True:
        result = kodi_window(window)
        if result:
            kodi_window(window, clear=True)
            LOG.debug('Received')
            return de_serialize(json.loads(result))
        if not _wait_poll(deadline):
            LOG.error('Timed out waiting for transfer from %s', source)
            return None


def _split_people(value):
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [v for v in str(value).split(' / ') if v]


def _apply_video_info(listitem, labels):
    """Apply legacy video infoLabels via InfoTagVideo (setInfo is deprecated).
    Mirrors the mapping widgets.create_listitem uses for real ListItems."""
    tags = listitem.getVideoInfoTag()
    labels = {k: v for k, v in labels.items() if v is not None}
    int_labels = {
        'dbid': tags.setDbId, 'year': tags.setYear, 'episode': tags.setEpisode,
        'season': tags.setSeason, 'top250': tags.setTop250,
        'tracknumber': tags.setTrackNumber, 'playcount': tags.setPlaycount,
        'duration': tags.setDuration, 'votes': tags.setVotes,
    }
    float_labels = {'rating': tags.setRating}
    str_labels = {
        'mpaa': tags.setMpaa, 'plot': tags.setPlot,
        'plotoutline': tags.setPlotOutline, 'title': tags.setTitle,
        'originaltitle': tags.setOriginalTitle, 'sorttitle': tags.setSortTitle,
        'tagline': tags.setTagLine, 'tvshowtitle': tags.setTvShowTitle,
        'premiered': tags.setPremiered, 'status': tags.setTvShowStatus,
        'set': tags.setSet, 'setoverview': tags.setSetOverview,
        'imdbnumber': tags.setIMDBNumber, 'code': tags.setProductionCode,
        'aired': tags.setFirstAired, 'lastplayed': tags.setLastPlayed,
        'album': tags.setAlbum, 'trailer': tags.setTrailer,
        'path': tags.setPath, 'filenameandpath': tags.setFilenameAndPath,
        'dateadded': tags.setDateAdded, 'mediatype': tags.setMediaType,
    }
    list_labels = {
        'artist': tags.setArtists, 'genre': tags.setGenres,
        'country': tags.setCountries, 'director': tags.setDirectors,
        'studio': tags.setStudios, 'writer': tags.setWriters,
        'tag': tags.setTags,
    }
    for label, setter in int_labels.items():
        if label in labels:
            setter(int(labels[label]))
    for label, setter in float_labels.items():
        if label in labels:
            setter(float(labels[label]))
    for label, setter in str_labels.items():
        if label in labels:
            setter(str(labels[label]))
    for label, setter in list_labels.items():
        if label in labels:
            setter(_split_people(labels[label]))
    if 'cast' in labels:
        tags.setCast([xbmc.Actor(str(name)) for name in _split_people(labels['cast'])])
    if 'castandrole' in labels:
        tags.setCast([xbmc.Actor(str(actor[0]), str(actor[1]))
                      for actor in labels['castandrole']])


def _apply_music_info(listitem, labels):
    """Apply legacy music infoLabels via InfoTagMusic."""
    tags = listitem.getMusicInfoTag()
    labels = {k: v for k, v in labels.items() if v is not None}
    if 'title' in labels:
        tags.setTitle(str(labels['title']))
    if 'album' in labels:
        tags.setAlbum(str(labels['album']))
    if 'artist' in labels:
        artist = labels['artist']
        tags.setArtist(' / '.join(_split_people(artist)) if isinstance(artist, (list, tuple)) else str(artist))
    if 'genre' in labels:
        tags.setGenres(_split_people(labels['genre']))
    if 'year' in labels:
        tags.setYear(int(labels['year']))
    if 'tracknumber' in labels:
        tags.setTrack(int(labels['tracknumber']))
    if 'discnumber' in labels:
        tags.setDisc(int(labels['discnumber']))
    if 'duration' in labels:
        tags.setDuration(int(labels['duration']))
    if 'rating' in labels:
        tags.setRating(float(labels['rating']))
    if 'playcount' in labels:
        tags.setPlayCount(int(labels['playcount']))
    if 'lyrics' in labels:
        tags.setLyrics(str(labels['lyrics']))
    if 'lastplayed' in labels:
        tags.setLastPlayed(str(labels['lastplayed']))
    if 'date' in labels:
        tags.setReleaseDate(str(labels['date']))
    if 'mediatype' in labels:
        tags.setMediaType(str(labels['mediatype']))


def _apply_stream_info(listitem, stream):
    ctype = stream.get('cType')
    values = stream.get('dictionary') or {}
    tags = listitem.getVideoInfoTag()
    if ctype == 'video':
        detail = xbmc.VideoStreamDetail()
        if 'codec' in values:
            detail.setCodec(str(values['codec']))
        if 'aspect' in values:
            detail.setAspect(round(float(values['aspect']), 2))
        if 'width' in values:
            detail.setWidth(int(values['width']))
        if 'height' in values:
            detail.setHeight(int(values['height']))
        if 'duration' in values:
            detail.setDuration(int(values['duration']))
        if 'stereomode' in values:
            detail.setStereoMode(str(values['stereomode']))
        if 'language' in values:
            detail.setLanguage(str(values['language']))
        if 'hdrtype' in values:
            detail.setHDRType(str(values['hdrtype']))
        tags.addVideoStream(detail)
    elif ctype == 'audio':
        detail = xbmc.AudioStreamDetail()
        if 'channels' in values:
            detail.setChannels(int(values['channels']))
        if 'codec' in values:
            detail.setCodec(str(values['codec']))
        if 'language' in values:
            detail.setLanguage(str(values['language']))
        tags.addAudioStream(detail)
    elif ctype == 'subtitle':
        detail = xbmc.SubtitleStreamDetail()
        if 'language' in values:
            detail.setLanguage(str(values['language']))
        tags.addSubtitleStream(detail)


def convert_pkc_to_listitem(pkc_listitem):
    """
    Insert a PKCListItem() and you will receive a valid XBMC listitem
    """
    data = pkc_listitem.data
    listitem = xbmcgui.ListItem(label=data.get('label'),
                                label2=data.get('label2'),
                                path=data.get('path'),
                                offscreen=True)
    if data['info']:
        try:
            info_type = str(data['info'].get('type') or '').lower()
            info_labels = data['info'].get('infoLabels') or {}
            if info_type == 'video':
                _apply_video_info(listitem, info_labels)
            elif info_type == 'music':
                _apply_music_info(listitem, info_labels)
            else:
                # Pictures (exif) has no InfoTag setters; keep legacy call
                listitem.setInfo(**data['info'])
        except Exception:
            # Never break playback over metadata: fall back to the
            # deprecated but still functional replay
            LOG.exception('InfoTag conversion failed, falling back to setInfo')
            listitem.setInfo(**data['info'])
    for stream in data['stream_info']:
        try:
            _apply_stream_info(listitem, stream)
        except Exception:
            LOG.exception('Stream detail conversion failed, falling back '
                          'to addStreamInfo')
            listitem.addStreamInfo(**stream)
    if data['art']:
        listitem.setArt(data['art'])
    for key, value in data['property'].items():
        listitem.setProperty(key, value)
    if data['subtitles']:
        listitem.setSubtitles(data['subtitles'])
    if data['contextmenu']:
        listitem.addContextMenuItems(data['contextmenu'])
    return listitem


class PKCListItem(object):
    """
    Imitates xbmcgui.ListItem and its functions. Pass along PKC_Listitem().data
    when pickling!

    WARNING: set/get path only via setPath and getPath! (not getProperty)
    """
    def __init__(self, label=None, label2=None, path=None, offscreen=True):
        self.data = {
            'stream_info': [],  # (type, values: dict { label: value })
            'art': {},  # dict
            'info': {},  # type: infoLabel (dict { label: value })
            'label': label,  # string
            'label2': label2,  # string
            'path': path,  # string
            'property': {},  # (key, value)
            'subtitles': [],  # strings
            'contextmenu': None
        }

    def addContextMenuItems(self, items):
        """
        Adds item(s) to the context menu for media lists.

        items : list - [(label, action,)*] A list of tuples consisting of label
        and action pairs.
            - label : string or unicode - item's label.
            - action : string or unicode - any built-in function to perform.
        replaceItes : [opt] bool - True=only your items will show/False=your
        items will be amdded to context menu(Default).

        List  of functions - http://kodi.wiki/view/List_of_Built_In_Functions

         *Note, You can use the above as keywords for arguments and skip
         certain optional arguments.

         Once you use a keyword, all following arguments require the keyword.
        """
        self.data['contextmenu'] = items

    def addStreamInfo(self, type, values):
        """
        Add a stream with details.
        type : string - type of stream(video/audio/subtitle).
        values : dictionary - pairs of { label: value }.

        - Video Values:
            - codec : string (h264)
            - aspect : float (1.78)
            - width : integer (1280)
            - height : integer (720)
            - duration : integer (seconds)
        - Audio Values:
            - codec : string (dts)
            - language : string (en)
            - channels : integer (2)
        - Subtitle Values:
            - language : string (en)
        """
        self.data['stream_info'].append({'cType': type, 'dictionary': values})

    def getLabel(self):
        """
        Returns the listitem label
        """
        return self.data.get('label')

    def getLabel2(self):
        """
        Returns the listitem label.
        """
        return self.data.get('label2')

    def getMusicInfoTag(self):
        """
        returns the MusicInfoTag for this item.
        """
        raise NotImplementedError

    def getProperty(self, key):
        """
        Returns a listitem property as a string, similar to an infolabel.
         key : string - property name.
         *Note, Key is NOT case sensitive.

         You can use the above as keywords for arguments and skip certain
         optional arguments.

         Once you use a keyword, all following arguments require the keyword.
        """
        return self.data['property'].get(key)

    def getVideoInfoTag(self):
        """
        returns the VideoInfoTag for this item
        """
        raise NotImplementedError

    def getdescription(self):
        """
        Returns the description of this PlayListItem
        """
        raise NotImplementedError

    def getduration(self):
        """
        Returns the duration of this PlayListItem
        """
        raise NotImplementedError

    def getfilename(self):
        """
        Returns the filename of this PlayListItem.
        """
        raise NotImplementedError

    def isSelected(self):
        """
        Returns the listitem's selected status
        """
        raise NotImplementedError

    def select(self):
        """
        Sets the listitem's selected status.
        selected : bool - True=selected/False=not selected
        """
        raise NotImplementedError

    def setArt(self, values):
        """
        Sets the listitem's art
        values : dictionary - pairs of { label: value }.

        Some default art values (any string possible):
            - thumb : string - image filename
            - poster : string - image filename
            - banner : string - image filename
            - fanart : string - image filename
            - clearart : string - image filename
            - clearlogo : string - image filename
            - landscape : string - image filename
            - icon : string - image filename
        """
        self.data['art'].update(values)

    def setContentLookup(self, enable):
        """
        Enable or disable content lookup for item.

        If disabled, HEAD requests to e.g determine mime type will not be sent.

        enable : bool
        """
        raise NotImplementedError

    def setInfo(self, type, infoLabels):
        """
        type : string - type of media(video/music/pictures).

        infoLabels : dictionary - pairs of { label: value }. *Note, To set
        pictures exif info, prepend 'exif:' to the label. Exif values must be
        passed as strings, separate value pairs with a comma. (eg.
        {'exif:resolution': '720,480'}

        See CPictureInfoTag::TranslateString in PictureInfoTag.cpp for valid
        strings. You can use the above as keywords for arguments and skip
        certain optional arguments.

        Once you use a keyword, all following arguments require the keyword.

        - General Values that apply to all types:
            - count : integer (12) - can be used to store an id for later, or
              for sorting purposes
            - size : long (1024) - size in bytes
            - date : string (d.m.Y / 01.01.2009) - file date

        - Video Values:
            - genre : string (Comedy)
            - year : integer (2009)
            - episode : integer (4)
            - season : integer (1)
            - top250 : integer (192)
            - tracknumber : integer (3)
            - rating : float (6.4) - range is 0..10
            - userrating : integer (9) - range is 1..10
            - watched : depreciated - use playcount instead
            - playcount : integer (2) - number of times this item has been
              played
            - overlay : integer (2) - range is 0..8. See GUIListItem.h for
              values
            - cast : list (["Michal C. Hall","Jennifer Carpenter"]) - if
              provided a list of tuples cast will be interpreted as castandrole
            - castandrole : list of tuples ([("Michael C.
              Hall","Dexter"),("Jennifer Carpenter","Debra")])
            - director : string (Dagur Kari)
            - mpaa : string (PG-13)
            - plot : string (Long Description)
            - plotoutline : string (Short Description)
            - title : string (Big Fan)
            - originaltitle : string (Big Fan)
            - sorttitle : string (Big Fan)
            - duration : integer (245) - duration in seconds
            - studio : string (Warner Bros.)
            - tagline : string (An awesome movie) - short description of movie
            - writer : string (Robert D. Siegel)
            - tvshowtitle : string (Heroes)
            - premiered : string (2005-03-04)
            - status : string (Continuing) - status of a TVshow
            - code : string (tt0110293) - IMDb code
            - aired : string (2008-12-07)
            - credits : string (Andy Kaufman) - writing credits
            - lastplayed : string (Y-m-d h:m:s = 2009-04-05 23:16:04)
            - album : string (The Joshua Tree)
            - artist : list (['U2'])
            - votes : string (12345 votes)
            - trailer : string (/home/user/trailer.avi)
            - dateadded : string (Y-m-d h:m:s = 2009-04-05 23:16:04)
            - mediatype : string - "video", "movie", "tvshow", "season",
              "episode" or "musicvideo"

        - Music Values:
            - tracknumber : integer (8)
            - discnumber : integer (2)
            - duration : integer (245) - duration in seconds
            - year : integer (1998)
            - genre : string (Rock)
            - album : string (Pulse)
            - artist : string (Muse)
            - title : string (American Pie)
            - rating : string (3) - single character between 0 and 5
            - lyrics : string (On a dark desert highway...)
            - playcount : integer (2) - number of times this item has been
              played
            - lastplayed : string (Y-m-d h:m:s = 2009-04-05 23:16:04)

        - Picture Values:
            - title : string (In the last summer-1)
            - picturepath : string (/home/username/pictures/img001.jpg)
            - exif : string (See CPictureInfoTag::TranslateString in
              PictureInfoTag.cpp for valid strings)
        """
        self.data['info'] = {'type': type, 'infoLabels': infoLabels}

    def setLabel(self, label):
        """
        Sets the listitem's label.
        label : string or unicode - text string.
        """
        self.data['label'] = label

    def setLabel2(self, label):
        """
        Sets the listitem's label2.
        label : string or unicode - text string.
        """
        self.data['label2'] = label

    def setMimeType(self, mimetype):
        """
        Sets the listitem's mimetype if known.
        mimetype : string or unicode - mimetype.

        If known prehand, this can (but does not have to) avoid HEAD requests
        being sent to HTTP servers to figure out file type.
        """
        raise NotImplementedError

    def setPath(self, path):
        """
        Sets the listitem's path.
        path : string or unicode - path, activated when item is clicked.

         *Note, You can use the above as keywords for arguments.
        """
        self.data['path'] = path

    def setProperty(self, key, value):
        """
        Sets a listitem property, similar to an infolabel.
            key : string - property name.
            value : string or unicode - value of property.
        *Note, Key is NOT case sensitive.

        You can use the above as keywords for arguments and skip certain
        optional arguments. Once you use a keyword, all following arguments
        require the keyword.

        Some of these are treated internally by XBMC, such as the
        'StartOffset' property, which is the offset in seconds at which to
        start playback of an item. Others may be used in the skin to add extra
        information, such as 'WatchedCount' for tvshow items
        """
        self.data['property'][key] = value

    def setSubtitles(self, subtitles):
        """
        Sets subtitles for this listitem. Pass in a list of filepaths

        example:
            - listitem.setSubtitles(['special://temp/example.srt',
              'http://example.com/example.srt' ])
        """
        self.data['subtitles'].extend(subtitles)
