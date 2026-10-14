import importlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from library_sync import isolate_modules


class ManualServerTests(unittest.TestCase):
    def setUp(self):
        isolate_modules(self)
        package = importlib.import_module('resources.lib')
        sys.modules.pop('resources.lib.initialsetup', None)
        self.responses = []
        self.use_https = False
        self.urls = []
        self.messages = []
        root = ET.fromstring('<MediaContainer machineIdentifier="server" '
                             'friendlyName="PMS"><Directory /></MediaContainer>')
        stubs = {
            'xbmc': {'executebuiltin': lambda *args: None},
            'resources.lib.utils': {
                'lang': str,
                'messageDialog': lambda *args: self.messages.append(args),
                'dialog': lambda *args, **kwargs: self.responses.pop(0),
                'yesno_dialog': lambda *args: self.use_https,
                'settings': lambda *args: ''},
            'resources.lib.path_ops': {'exists': lambda path: True},
            'resources.lib.migration': {},
            'resources.lib.downloadutils': {
                'DownloadUtils': object,
                'exceptions': SimpleNamespace(SSLError=type('SSLError', (Exception,), {}))},
            'resources.lib.plex_functions': {
                'GetMachineIdentifier': lambda url: self.urls.append(url) or 'server',
                'pms_root': lambda url, token: root},
            'resources.lib.plex_tv': {},
            'resources.lib.json_rpc': {},
            'resources.lib.app': {},
            'resources.lib.variables': {'EXTERNAL_SUBTITLE_TEMP_PATH': '/unused'},
        }
        for name, attributes in stubs.items():
            module = types.ModuleType(name)
            vars(module).update(attributes)
            sys.modules[name] = module
            if name.startswith('resources.lib.'):
                setattr(package, name.rsplit('.', 1)[1], module)
        self.setup = importlib.import_module('resources.lib.initialsetup')

    def test_manual_scheme_matches_https_answer(self):
        for use_https, expected in ((False, 'http'), (True, 'https')):
            with self.subTest(https=use_https):
                self.use_https = use_https
                self.responses = ['pms.example', '32400']
                result = self.setup.InitialSetup.enter_new_pms_address()
                self.assertEqual(result['scheme'], expected)
                self.assertEqual(result['baseURL'], expected + '://pms.example:32400')

    def test_pasted_url_uses_host_and_separate_port_answer(self):
        for address, host, url in (
                (' https://pms.example:1234/web/index.html?q=x ',
                 'pms.example', 'http://pms.example:32400'),
                ('pms.example:1234/web', 'pms.example', 'http://pms.example:32400'),
                ('2001:db8::1', '2001:db8::1', 'http://[2001:db8::1]:32400'),
                ('http://[2001:db8::1]:1234/web', '2001:db8::1',
                 'http://[2001:db8::1]:32400')):
            with self.subTest(address=address):
                self.responses = [address, '32400']
                result = self.setup.InitialSetup.enter_new_pms_address()
                self.assertEqual(result['ip'], host)
                self.assertEqual(result['baseURL'], url)
                self.assertEqual(self.urls[-1], url)

    def test_invalid_address_never_contacts_server(self):
        for address in ('   ', '/web', 'https://[broken'):
            with self.subTest(address=address):
                self.responses = [address, '32400']
                self.assertFalse(self.setup.InitialSetup.enter_new_pms_address())
        self.assertEqual(self.urls, [])
