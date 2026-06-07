import importlib
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class SkipMarkerStateTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop('resources.lib.skip_marker_state', None)
        self.state = importlib.import_module('resources.lib.skip_marker_state')

    def test_builds_countdown_properties_for_visible_marker(self):
        properties = self.state.build_properties(
            marker_type='credits',
            marker_message='Skip credits',
            marker_end=125.25,
            creation_time=100.0,
            progress=103.2,
            auto_hide_seconds=10,
            toast_visible=True,
        )

        self.assertEqual(properties['available'], '1')
        self.assertEqual(properties['type'], 'credits')
        self.assertEqual(properties['label'], 'Skip credits')
        self.assertEqual(properties['end'], '125.25')
        self.assertEqual(properties['toast_visible'], '1')
        self.assertEqual(properties['hide_remaining'], '7')
        self.assertEqual(properties['hide_progress_percent'], '68.0')
        self.assertEqual(properties['hide_progress_frame'], '68')

    def test_keeps_osd_available_after_toast_auto_hides(self):
        properties = self.state.build_properties(
            marker_type='intro',
            marker_message='Skip intro',
            marker_end=45.0,
            creation_time=30.0,
            progress=43.0,
            auto_hide_seconds=10,
            toast_visible=False,
        )

        self.assertEqual(properties['available'], '1')
        self.assertEqual(properties['toast_visible'], '')
        self.assertEqual(properties['hide_remaining'], '0')
        self.assertEqual(properties['hide_progress_percent'], '0.0')
        self.assertEqual(properties['hide_progress_frame'], '0')

    def test_clear_properties_targets_every_public_marker_property(self):
        cleared = self.state.clear_properties()

        self.assertEqual(set(cleared), set(self.state.PROPERTY_KEYS))
        self.assertTrue(all(value == '' for value in cleared.values()))


if __name__ == '__main__':
    unittest.main()
