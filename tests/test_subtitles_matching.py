import importlib.util
import sys
import types
import unittest
import xml.etree.ElementTree as etree
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EN = ('english', 'en', 'eng', 'eng')
DE = ('german', 'de', 'deu', 'ger')


def _load_subtitles_module():
    import resources.lib as package

    def stub(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        sys.modules[name] = module
        setattr(package, name.rsplit('.', 1)[1], module)
        return module

    stub('resources.lib.app')
    stub('resources.lib.path_ops')
    stub('resources.lib.variables')
    stub('resources.lib.exceptions', SubtitleError=Exception)
    spec = importlib.util.spec_from_file_location(
        'resources.lib.subtitles',
        REPO_ROOT / 'resources' / 'lib' / 'subtitles.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['resources.lib.subtitles'] = module
    spec.loader.exec_module(module)
    return module


def kodi_sub(iso, forced, codec='srt'):
    return {'iso': iso, 'language': '', 'forced': forced, 'codec': codec,
            'file': ''}


def plex_sub(name, forced=None, language=None, codec='srt'):
    stream = etree.Element('Stream', {'id': name, 'codec': codec})
    if forced is not None:
        stream.set('forced', forced)
    if language:
        stream.set('languageTag', language)
    return stream


class ReorderPlexStreamsTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        import resources.lib as package
        saved = dict(vars(package))

        def restore():
            for name in list(vars(package)):
                if name not in saved:
                    delattr(package, name)
            for name, value in saved.items():
                setattr(package, name, value)
        self.addCleanup(restore)
        self.subtitles = _load_subtitles_module()

    def _ids(self, kodi_subs, plex_streams):
        order = self.subtitles.reorder_plex_streams(plex_streams, kodi_subs)
        return [x.get('id') for x in order]

    def test_forced_zero_does_not_beat_a_forced_stream(self):
        for kodi_subs in ([kodi_sub(EN, True)],
                          [kodi_sub(None, True)]):
            # A plain subtitle listed first must not beat a real forced one
            forced = plex_sub('forced', forced='1', language='en')
            plain = plex_sub('plain', forced='0', language='en')
            self.assertEqual(self._ids(kodi_subs, [plain, forced]),
                             ['forced'])

    def test_forced_zero_and_missing_flag_are_not_forced(self):
        kodi_subs = [kodi_sub(EN, False)]
        streams = [plex_sub('forced', forced='1', language='en'),
                   plex_sub('zero', forced='0', language='en')]
        self.assertEqual(self._ids(kodi_subs, streams), ['zero'])
        streams = [plex_sub('forced', forced='1', language='en'),
                   plex_sub('missing', language='en')]
        self.assertEqual(self._ids(kodi_subs, streams), ['missing'])

    def test_pass_priority(self):
        # Pass 1 (language, codec, forced) beats everything
        self.assertEqual(
            self._ids([kodi_sub(EN, True)],
                      [plex_sub('plain', '0', 'en'),
                       plex_sub('codec_only', '1'),
                       plex_sub('forced', '1', 'en')]),
            ['forced'])
        # Pass 2 (language, codec, same forced flag) beats pass 3
        self.assertEqual(
            self._ids([kodi_sub(EN, False)],
                      [plex_sub('forced', '1', 'en'),
                       plex_sub('plain', '0', 'en')]),
            ['plain'])
        # Pass 3 (language, codec, any forced flag) beats codec-only passes
        self.assertEqual(
            self._ids([kodi_sub(EN, False)],
                      [plex_sub('no_language', '0'),
                       plex_sub('forced', '1', 'en')]),
            ['forced'])
        # Pass 4 (codec, forced, language unknown) beats pass 5 and 6
        self.assertEqual(
            self._ids([kodi_sub(EN, True)],
                      [plex_sub('plain', '0'), plex_sub('forced', '1')]),
            ['forced'])
        # Pass 5 (codec, same forced flag) beats pass 6
        self.assertEqual(
            self._ids([kodi_sub(None, False)],
                      [plex_sub('forced', '1'), plex_sub('plain', '0')]),
            ['plain'])
        # Pass 6: codec alone, but never against a different known language
        self.assertEqual(
            self._ids([kodi_sub(EN, False)],
                      [plex_sub('german', '0', 'de'),
                       plex_sub('forced', '1')]),
            ['forced'])
        self.assertEqual(
            self._ids([kodi_sub(EN, False)],
                      [plex_sub('german', '0', 'de')]),
            [None])

    def test_earlier_pass_claims_stream_for_later_kodi_sub(self):
        # Kodi sub 0 only matches in a later pass, Kodi sub 1 in the first
        self.assertEqual(
            self._ids([kodi_sub(None, False), kodi_sub(EN, True)],
                      [plex_sub('forced', '1', 'en')]),
            [None, 'forced'])


if __name__ == '__main__':
    unittest.main()
