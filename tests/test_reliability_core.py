"""Offline declaration in downloadutils and bounded thread suspension.

downloadutils/backgroundthread previously had no dedicated tests: a single
failed download declared the PMS dead, and suspend(block=True) could wait
forever on a thread stuck in a blocking call.
"""
import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def stub(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def load_module(name):
    sys.modules.pop(name, None)
    package = importlib.import_module('resources.lib')
    package.__dict__.pop(name.rsplit('.', 1)[-1], None)
    return importlib.import_module(name)


class FakeSession(object):
    def __init__(self, exc=None, response=None):
        self.exc = exc
        self.response = response

    def get(self, **kwargs):
        if self.exc:
            raise self.exc
        return self.response


class DownloadOfflineTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        importlib.import_module('resources.lib')
        self.conn = SimpleNamespace(server='http://pms:32400',
                                    verify_ssl_cert=False,
                                    ssl_cert_path=None,
                                    online=True)
        stub('resources.lib.app', CONN=self.conn,
             ACCOUNT=SimpleNamespace(log_out=lambda: None))
        stub('resources.lib.utils',
             etree=SimpleNamespace(fromstring=lambda content: content),
             lang=lambda code: str(code),
             dialog=lambda *args, **kwargs: None)
        stub('resources.lib.clientinfo')
        self.downloadutils = load_module('resources.lib.downloadutils')
        # Borg shared state leaks between tests otherwise
        self.downloadutils.DownloadUtils._shared_state.clear()
        self.du = self.downloadutils.DownloadUtils()
        self.du.count_error = 0
        self.du.count_unauthorized = 0

    def download(self):
        return self.du.downloadUrl('{server}/library/sections')

    def test_single_connection_error_does_not_declare_pms_dead(self):
        self.du.s = FakeSession(exc=requests.exceptions.ConnectionError())
        self.download()
        self.assertTrue(self.conn.online)
        self.assertEqual(self.du.count_error, 1)

    def test_repeated_connection_errors_declare_pms_dead(self):
        self.du.s = FakeSession(exc=requests.exceptions.Timeout())
        for _ in range(self.du.connection_attempts - 1):
            self.download()
            self.assertTrue(self.conn.online)
        self.download()
        self.assertFalse(self.conn.online)

    def test_ssl_error_never_declares_pms_dead(self):
        self.du.s = FakeSession(exc=requests.exceptions.SSLError())
        for _ in range(self.du.connection_attempts + 2):
            self.download()
        self.assertTrue(self.conn.online)
        self.assertEqual(self.du.count_error, 0)

    def test_success_resets_the_failure_count(self):
        self.du.s = FakeSession(exc=requests.exceptions.ConnectionError())
        self.download()
        self.assertEqual(self.du.count_error, 1)
        response = SimpleNamespace(status_code=204, content=b'')
        self.du.s = FakeSession(response=response)
        self.download()
        self.assertEqual(self.du.count_error, 0)
        self.assertTrue(self.conn.online)


class SuspendBoundTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        importlib.import_module('resources.lib')
        stub('resources.lib.utils')
        stub('resources.lib.app',
             APP=SimpleNamespace(stop_pkc=False))
        stub('resources.lib.variables')
        self.bg = load_module('resources.lib.backgroundthread')

    def test_blocking_suspend_waits_with_a_timeout(self):
        thread = self.bg.KillableThread()
        waited = []

        class FakeEvent(object):
            def wait(self, timeout=None):
                waited.append(timeout)
                return False

        thread._suspension_reached = FakeEvent()
        thread.suspend(block=True)
        self.assertEqual(len(waited), 1)
        self.assertIsNotNone(waited[0])
        self.assertLessEqual(waited[0], 60)


if __name__ == '__main__':
    unittest.main()
