#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Processes Plex companion inputs from the plexbmchelper to Kodi commands
"""
from logging import getLogger
from xbmc import Player

from . import plex_functions as PF
from . import json_rpc as js
from . import variables as v
from . import app

###############################################################################

LOG = getLogger('PLEX.companion')

###############################################################################


def skip_to(params):
    """
    Skip to a specific playlist position.

    Does not seem to be implemented yet by Plex!
    """
    playqueue_item_id = params.get('playQueueItemID')
    _, plex_id = PF.GetPlexKeyNumber(params.get('key'))
    LOG.debug('Skipping to playQueueItemID %s, plex_id %s',
              playqueue_item_id, plex_id)
    found = True
    for player in list(js.get_players().values()):
        playqueue = app.PLAYQUEUES[player['playerid']]
        for i, item in enumerate(playqueue.items):
            if item.id == playqueue_item_id:
                found = True
                break
        else:
            for i, item in enumerate(playqueue.items):
                if item.plex_id == plex_id:
                    found = True
                    break
        if found is True:
            Player().play(playqueue.kodi_pl, None, False, i)
        else:
            LOG.error('Item not found to skip to')


def convert_alexa_to_companion(dictionary):
    """
    The params passed by Alexa must first be converted to Companion talk
    """
    for key in list(dictionary):
        if key in v.ALEXA_TO_COMPANION:
            dictionary[v.ALEXA_TO_COMPANION[key]] = dictionary[key]
            del dictionary[key]


_COMMON_COMMANDS = {
    'player/playback/play': 'play',
    'player/playback/pause': 'pause',
    'player/playback/stop': 'stop',
    'player/playback/stepForward': 'smallforward',
    'player/playback/stepBack': 'smallbackward',
    'player/playback/skipNext': 'skipnext',
    'player/playback/skipPrevious': 'skipprevious',
    'player/navigation/moveUp': 'input_up',
    'player/navigation/moveDown': 'input_down',
    'player/navigation/moveLeft': 'input_left',
    'player/navigation/moveRight': 'input_right',
    'player/navigation/select': 'input_select',
    'player/navigation/home': 'input_home',
    'player/navigation/back': 'input_back',
}


def process_common_command(request_path, params):
    """Dispatch playback and navigation commands shared by both gateways."""
    if request_path == 'player/playback/seekTo':
        js.seek_to(float(params.get('offset', 0.0)) / 1000.0)
    else:
        method = _COMMON_COMMANDS.get(request_path)
        if method is None:
            return False
        getattr(js, method)()
    return True


def process_command(request_path, params):
    """
    queue: Queue() of PlexCompanion.py
    """
    if params.get('deviceName') == 'Alexa':
        convert_alexa_to_companion(params)
    LOG.debug('Received request_path: %s (%s params)',
              request_path, len(params))
    if request_path == 'player/playback/playMedia':
        # We need to tell service.py
        action = 'alexa' if params.get('deviceName') == 'Alexa' else 'playlist'
        app.APP.companion_queue.put({
            'action': action,
            'data': params
        })
    elif request_path == 'player/playback/refreshPlayQueue':
        app.APP.companion_queue.put({
            'action': 'refreshPlayQueue',
            'data': params
        })
    elif request_path == "player/playback/setParameters":
        if 'volume' in params:
            js.set_volume(int(params['volume']))
        else:
            LOG.error('Unknown parameters for %s (%s params)',
                      request_path, len(params))
            return False
    elif request_path == "player/playback/skipTo":
        skip_to(params)
    elif request_path == "player/playback/setStreams":
        app.APP.companion_queue.put({
            'action': 'setStreams',
            'data': params
        })
    elif not process_common_command(request_path, params):
        LOG.error('Unknown request path: %s', request_path)
        return False
    return True
