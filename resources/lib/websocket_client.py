#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger
import json
import socket

from . import websocket
from . import backgroundthread, app, variables as v, utils, companion

log = getLogger('PLEX.websocket')

PMS_PATH = '/:/websockets/notifications'

PMS_INTERESTING_MESSAGE_TYPES = ('playing', 'timeline', 'activity')
SETTINGS_STRING = '_status'


def get_pms_uri():
    uri = app.CONN.server
    if not uri:
        return
    # Get the appropriate prefix for the websocket
    if uri.startswith('https'):
        uri = "wss%s" % uri[5:]
    else:
        uri = "ws%s" % uri[4:]
    uri += PMS_PATH
    log.debug('Connecting to PMS websocket')
    if app.ACCOUNT.pms_token:
        uri += '?X-Plex-Token=' + app.ACCOUNT.pms_token
    return uri


def get_alexa_uri():
    if not app.ACCOUNT.plex_token:
        return
    return (f'wss://pubsub.plex.tv/sub/websockets/{app.ACCOUNT.plex_user_id}/'
            f'{v.PKC_MACHINE_IDENTIFIER}?'
            f'X-Plex-Token={app.ACCOUNT.plex_token}')


def pms_on_message(ws, message):
    """
    Called when we receive a message from the PMS, e.g. when a new library
    item has been added.
    """
    try:
        message = json.loads(message)
    except ValueError as err:
        log.error('Error decoding PMS websocket message (%s)',
                  type(err).__name__)
        return
    try:
        message = message['NotificationContainer']
        typus = message['type']
    except KeyError as err:
        log.error('Could not parse PMS message (%s)', type(err).__name__)
        return
    # Triage
    if typus not in PMS_INTERESTING_MESSAGE_TYPES:
        # Drop everything we're not interested in
        return
    else:
        # Put PMS message on queue and let libsync take care of it
        app.APP.websocket_queue.put(message)


def alexa_on_message(ws, message):
    """
    Called when we receive a message from Alexa
    """
    log.debug('Alexa message received')
    try:
        message = utils.etree.fromstring(message)
    except Exception as err:
        log.error('Error decoding message from Alexa (%s)',
                  type(err).__name__)
        return
    try:
        if message.attrib['command'] == 'processRemoteControlCommand':
            message = message[0]
        else:
            log.error('Unknown Alexa message received')
            return
        companion.process_command(message.attrib['path'][1:], message.attrib)
    except Exception as err:
        log.error('Could not parse Alexa message (%s)',
                  type(err).__name__)


def on_error(ws, error):
    if ws.should_cancel() or ws.should_suspend():
        return
    status = ws.name + SETTINGS_STRING
    if isinstance(error, IOError):
        # We are probably offline
        log.debug('%s: IOError connecting', ws.name)
        # Status = IOError - not connected
        utils.settings(status, value=utils.lang(39092))
        ws.sleep_cycle()
    elif isinstance(error, websocket.WebSocketTimeoutException):
        log.debug('%s: WebSocketTimeoutException', ws.name)
        # Status = 'Timeout - not connected'
        utils.settings(status, value=utils.lang(39091))
        ws.sleep_cycle()
    elif isinstance(error, websocket.WebSocketConnectionClosedException):
        log.debug('%s: WebSocketConnectionClosedException', ws.name)
        # Status = Not connected
        utils.settings(ws.name + SETTINGS_STRING, value=utils.lang(15208))
    elif isinstance(error, websocket.WebSocketBadStatusException):
        # Most likely Alexa not connecting, throwing a 403
        log.debug('%s: got a bad HTTP status (%s)',
                  ws.name, type(error).__name__)
        # Status = <value of exception>
        utils.settings(status, value=str(error))
        ws.sleep_cycle()
    elif isinstance(error, websocket.WebSocketException):
        log.error('%s: got another websocket exception (%s)',
                  ws.name, type(error).__name__)
        # Status = Error
        utils.settings(status, value=utils.lang(257))
        ws.sleep_cycle()
    elif isinstance(error, SystemExit):
        log.debug('%s: SystemExit detected', ws.name)
        # Status = Not connected
        utils.settings(ws.name + SETTINGS_STRING, value=utils.lang(15208))
    else:
        log.error('%s: got an unexpected exception (%s)',
                  ws.name, type(error).__name__)
        # Status = Error
        utils.settings(status, value=utils.lang(257))


def on_close(ws):
    """
    This does not seem to get called by our websocket client :-(
    """
    log.debug('%s: connection closed', ws.name)
    # Status = Not connected
    utils.settings(ws.name + SETTINGS_STRING, value=utils.lang(15208))


def on_open(ws):
    if ws.should_cancel() or ws.should_suspend():
        ws.close()
        return
    log.debug('%s: connected', ws.name)
    # Status = Connected
    utils.settings(ws.name + SETTINGS_STRING, value=utils.lang(13296))
    ws.sleeptime = 0


class PlexWebSocketApp(websocket.WebSocketApp,
                       backgroundthread.KillableThread):
    def __init__(self, **kwargs):
        self.sleeptime = 0
        backgroundthread.KillableThread.__init__(self)
        websocket.WebSocketApp.__init__(self, self.get_uri(), **kwargs)

    def sleep_cycle(self):
        """
        Sleeps for 2^self.sleeptime where sleeping period will be doubled with
        each unsuccessful connection attempt.
        Will sleep at most 64 seconds
        """
        self.sleep(2 ** self.sleeptime)
        if self.sleeptime < 6:
            self.sleeptime += 1

    def sleep(self, timeout):
        self._is_not_asleep.clear()
        try:
            # Check after clear so cancellation cannot lose the wake signal.
            if not self.should_cancel() and not self._suspended:
                self._is_not_asleep.wait(timeout)
        finally:
            self._is_not_asleep.set()

    def close(self, **kwargs):
        # Do not wait for a peer close frame. Wake selectors and partial reads,
        # including the HTTP upgrade before WebSocket.connected becomes True.
        self.keep_running = False
        ws = self.sock
        if ws is None:
            return
        try:
            if ws.sock is not None:
                ws.sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
        try:
            ws.shutdown()
        except (AttributeError, OSError):
            pass

    def suspend(self, block=False, timeout=None):
        backgroundthread.KillableThread.suspend(self, False, timeout)
        self.close()
        if block and not self._suspension_reached.wait(35):
            log.warning('Thread %s did not suspend within 35s', self.name)

    def cancel(self):
        backgroundthread.KillableThread.cancel(self)
        self.close()

    def run(self):
        """
        Ensure that sockets will be closed no matter what
        """
        log.info("----===## Starting %s ##===----", self.name)
        app.APP.register_thread(self)
        try:
            self._run()
        except RuntimeError:
            pass
        except Exception as err:
            log.error('%s: WebSocket run failed (%s)',
                      self.name, type(err).__name__)
        finally:
            self.close()
            if self._enabled:
                # Status = Not connected
                message = utils.lang(15208)
            else:
                # Status = Disabled
                message = utils.lang(24023)
            try:
                utils.settings(self.name + SETTINGS_STRING,
                               value=message)
            except RuntimeError as err:
                # PKC is being replaced (add-on update): its settings are gone
                log.debug('%s: could not set the connection status (%s)',
                          self.name, type(err).__name__)
            app.APP.deregister_thread(self)
            log.info("----===## %s stopped ##===----", self.name)

    def _run(self):
        while not self.should_cancel() and self._enabled:
            # In the event the server goes offline
            while self.should_suspend():
                # We will be caught in this loop if either another thread
                # called the suspend() method, thus setting _suspended = True
                # OR if there any other conditions to not open a websocket
                # connection - see methods should_suspend() below
                # Status = Suspended - not connected
                self.set_suspension_settings_status()
                if self.wait_while_suspended():
                    # Abort was requested while waiting. We should exit
                    return
                if not self._suspended:
                    # because wait_while_suspended will return instantly if
                    # this thread did not get suspended from another thread
                    self.sleep_cycle()
            self.url = self.get_uri()
            if not self.url:
                self.sleep_cycle()
                continue
            try:
                # Bound connect/upgrade reads and selector waits per connection.
                self.run_forever(socket_timeout=3, ping_timeout=1)
            finally:
                self.close()
                # Only the run thread discards the socket. A concurrent close
                # must leave the in-flight connect available for cleanup.
                self.sock = None


class PMSWebsocketApp(PlexWebSocketApp):
    name = 'pms_websocket'

    def __init__(self, *args, **kwargs):
        self._enabled = utils.settings('enableBackgroundSync') == 'true'
        super().__init__(*args, **kwargs)

    def get_uri(self):
        return get_pms_uri()

    def set_suspension_settings_status(self):
        if utils.settings('enableBackgroundSync') != 'true':
            # Status = Disabled
            utils.settings(self.name + SETTINGS_STRING,
                           value=utils.lang(24023))
        else:
            # Status = 'Suspended - not connected'
            utils.settings(self.name + SETTINGS_STRING,
                           value=utils.lang(39093))


class AlexaWebsocketApp(PlexWebSocketApp):
    name = 'alexa_websocket'

    def __init__(self, *args, **kwargs):
        self._enabled = utils.settings('enable_alexa') == 'true'
        super().__init__(*args, **kwargs)

    def get_uri(self):
        return get_alexa_uri()

    def should_suspend(self):
        """
        Returns True if the thread needs to suspend.
        """
        return self._suspended or \
            app.ACCOUNT.restricted_user or \
            not app.ACCOUNT.plex_token

    def set_suspension_settings_status(self):
        if utils.settings('enable_alexa') != 'true':
            # Status = Disabled
            utils.settings(self.name + SETTINGS_STRING,
                           value=utils.lang(24023))
        elif app.ACCOUNT.restricted_user:
            # Status = Managed Plex User - not connected
            utils.settings(self.name + SETTINGS_STRING,
                           value=utils.lang(39094))
        elif not app.ACCOUNT.plex_token:
            # Status = Not logged in to plex.tv
            utils.settings(self.name + SETTINGS_STRING,
                           value=utils.lang(39226))
        else:
            # Status = 'Suspended - not connected'
            utils.settings(self.name + SETTINGS_STRING,
                           value=utils.lang(39093))


def get_pms_websocketapp():
    return PMSWebsocketApp(on_open=on_open,
                           on_message=pms_on_message,
                           on_error=on_error,
                           on_close=on_close)


def get_alexa_websocketapp():
    return AlexaWebsocketApp(on_open=on_open,
                             on_message=alexa_on_message,
                             on_error=on_error,
                             on_close=on_close)
