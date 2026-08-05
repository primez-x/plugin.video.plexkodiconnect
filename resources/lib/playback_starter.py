#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger
from time import monotonic

from . import utils, playback, transfer, backgroundthread, app
from .contextmenu import menu

###############################################################################

LOG = getLogger('PLEX.playback_starter')

###############################################################################


class PlaybackTask(backgroundthread.Task):
    """
    Processes new plays
    """
    def __init__(self, command):
        self.command = command
        super(PlaybackTask, self).__init__()

    def run(self):
        LOG.debug('Starting PlaybackTask with %s', self.command)
        item = self.command
        try:
            _, params = item.split('?', 1)
        except ValueError:
            # E.g. other add-ons scanning for Extras folder
            LOG.debug('Detected 3rd party add-on call - ignoring')
            transfer.send(True)
            # Wait for default.py to have completed xbmcplugin.setResolvedUrl()
            transfer.wait_for_transfer(source='default')
            return
        params = dict(utils.parse_qsl(params))
        mode = params.get('mode')
        resolve = False if params.get('handle') == '-1' else True
        LOG.debug('Received mode: %s, params: %s', mode, params)
        if mode == 'play':
            upnext_token = params.get('pkc_upnext')
            with app.APP.lock_playqueues:
                expected = getattr(
                    app.PLAYSTATE, 'expected_upnext_handoff', None)
                outgoing = getattr(app.PLAYSTATE, 'item', None)
                if upnext_token and expected and outgoing and \
                        upnext_token == expected.get('token') and \
                        str(params.get('plex_id')) == str(
                            expected.get('next_plex_id')) and \
                        outgoing.kodi_id == expected.get('previous_kodi_id') and \
                        outgoing.kodi_type == expected.get(
                            'previous_kodi_type') and \
                        str(outgoing.plex_id) == str(
                            expected.get('previous_plex_id')) and \
                        getattr(outgoing, 'pkc_playback_generation', None) == \
                        expected.get('previous_generation'):
                    app.PLAYSTATE.started_upnext_handoff = {
                        'token': upnext_token,
                        'plex_id': params.get('plex_id'),
                        'previous_kodi_id': outgoing.kodi_id,
                        'previous_kodi_type': outgoing.kodi_type,
                        'previous_plex_id': outgoing.plex_id,
                        'previous_generation': getattr(
                            outgoing, 'pkc_playback_generation', None),
                        'created_at': monotonic(),
                    }
                else:
                    app.PLAYSTATE.started_upnext_handoff = None
            if params.get('force_transcode') == '1':
                app.PLAYSTATE.force_transcode = True
            if params.get('pms_play') == '1':
                app.PLAYSTATE.context_menu_play = True
            if params.get('resume'):
                resume = params.get('resume') == '1'
            else:
                resume = None
            playback.playback_triage(plex_id=params.get('plex_id'),
                                     plex_type=params.get('plex_type'),
                                     path=params.get('path'),
                                     resolve=resolve,
                                     resume=resume)
        elif mode == 'plex_node':
            playback.process_indirect(params['key'],
                                      params['offset'],
                                      resolve=resolve)
        elif mode == 'context_menu':
            menu.ContextMenu(kodi_id=params.get('kodi_id'),
                             kodi_type=params.get('kodi_type'))
        LOG.debug('Finished PlaybackTask')
