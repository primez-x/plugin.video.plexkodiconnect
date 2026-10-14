import importlib
import socket
import sys
import types
import unittest
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

from library_sync import isolate_modules


class WebsocketShutdownTests(unittest.TestCase):
    def setUp(self):
        isolate_modules(self)
        package = importlib.import_module('resources.lib')
        for name in ('websocket_client', 'backgroundthread', 'websocket'):
            sys.modules.pop('resources.lib.' + name, None)
            package.__dict__.pop(name, None)
        self.active = set()
        stubs = {
            'utils': {'settings': lambda *args, **kwargs: 'true', 'lang': str},
            'app': {
                'APP': SimpleNamespace(stop_pkc=False,
                    register_thread=self.active.add, deregister_thread=self.active.discard),
                'ACCOUNT': SimpleNamespace(plex_token='test', plex_user_id='test',
                                           restricted_user=False)},
            'variables': {'PKC_MACHINE_IDENTIFIER': 'test'},
            'companion': {},
        }
        for name, attributes in stubs.items():
            module = types.ModuleType('resources.lib.' + name)
            vars(module).update(attributes)
            sys.modules[module.__name__] = module
            setattr(package, name, module)
        self.module = importlib.import_module('resources.lib.websocket_client')
        self.core = importlib.import_module('resources.lib.websocket._core')
        self.ws = self.module.get_alexa_websocketapp()
        self.opened = Event()
        self.opens = 0
        self.ws.on_close = lambda *args: None

        def on_open(ws):
            self.opens += 1
            self.module.on_open(ws)
            self.opened.set()

        self.ws.on_open = on_open
        self.peers = []
        transport = patch.object(self.core, 'connect', side_effect=self.connect)
        transport.start()
        self.addCleanup(transport.stop)
        self.addCleanup(self.cleanup_thread)

    def cleanup_thread(self):
        for peer in self.peers:
            peer.close()
        self.ws.cancel()
        if self.ws.ident is not None:
            self.ws.join(4)
        self.assertFalse(self.ws.is_alive(), 'Websocket thread leaked')

    def connect(self, url, options, proxy, supplied_socket):
        client, peer = socket.socketpair()
        client.settimeout(options.timeout)
        self.peers.append(peer)
        return client, ('example.test', 80, '/')

    def start_connected(self, ssl_dispatcher=False):
        self.enterContext(patch.object(self.core, 'connect', side_effect=self.connect))
        self.enterContext(patch.object(self.core, 'handshake', return_value=SimpleNamespace(
            status=101, headers={}, subprotocol=None)))
        if ssl_dispatcher:
            connect = self.connect

            class PendingSocket:
                def __init__(self, sock):
                    self.sock = sock

                def pending(self):
                    return 0

                def __getattr__(self, name):
                    return getattr(self.sock, name)

            def ssl_connect(*args):
                sock, address = connect(*args)
                return PendingSocket(sock), address

            self.core.connect.side_effect = ssl_connect
            self.enterContext(patch.object(self.core.WebSocket, 'is_ssl', return_value=True))
        self.ws.start()
        self.assertTrue(self.opened.wait(1), 'Websocket never opened')

    def test_cancel_interrupts_idle_plain_dispatcher(self):
        self.start_connected()
        start = monotonic()
        self.ws.cancel()
        self.ws.join(2)
        self.assertFalse(self.ws.is_alive())
        self.assertLess(monotonic() - start, 2)
        self.assertFalse(self.active)
        self.assertEqual(self.opens, 1)

    def test_cancel_interrupts_idle_ssl_dispatcher(self):
        self.start_connected(ssl_dispatcher=True)
        start = monotonic()
        self.ws.cancel()
        self.ws.join(2)
        self.assertFalse(self.ws.is_alive())
        self.assertLess(monotonic() - start, 2)

    def test_cancel_interrupts_incomplete_http_handshake(self):
        handshake_started = Event()

        def connect(*args):
            result = self.connect(*args)
            handshake_started.set()
            return result

        self.enterContext(patch.object(self.core, 'connect', side_effect=connect))
        self.ws.start()
        self.assertTrue(handshake_started.wait(1))
        # Wait for the upgrade request; the peer never sends an HTTP reply.
        self.peers[0].settimeout(1)
        self.assertIn(b'Upgrade: websocket', self.peers[0].recv(4096))
        self.ws.cancel()
        self.ws.join(2)
        self.assertFalse(self.ws.is_alive())
        self.assertEqual(self.opens, 0)

    def test_connection_uses_bounded_timeout(self):
        timeouts = []

        def connect(url, options, proxy, supplied_socket):
            timeouts.append(options.timeout)
            self.ws.cancel()
            raise self.module.websocket.WebSocketTimeoutException('No connection')

        with patch.object(self.core, 'connect', side_effect=connect):
            self.ws.start()
            self.ws.join(2)
        self.assertFalse(self.ws.is_alive())
        self.assertEqual(len(timeouts), 1)
        self.assertIsNotNone(timeouts[0])
        self.assertLessEqual(timeouts[0], 3)

    def test_cancel_before_backoff_wait_cannot_lose_wake(self):
        self.ws.sleeptime = 6
        self.ws._is_not_asleep = SimpleNamespace(
            clear=self.ws.cancel,
            wait=lambda timeout: self.fail('Backoff after cancellation'),
            set=lambda: None)
        self.ws.sleep_cycle()
        self.assertTrue(self.ws.should_cancel())

    def test_suspend_then_resume_reconnects(self):
        self.start_connected()
        self.ws.suspend(block=True)
        self.assertTrue(self.ws.is_suspended())
        self.assertTrue(self.ws.is_alive())
        self.opened.clear()
        self.ws.resume()
        self.assertTrue(self.opened.wait(2.5), 'Websocket failed to reconnect')
        self.assertEqual(self.opens, 2)
