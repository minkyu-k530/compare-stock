import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.measure_video_pts import measure, parse_pts, validate_numbered_frames


class VideoPtsTests(unittest.TestCase):
    def test_variable_frame_intervals_use_pts_difference(self):
        pts = parse_pts('{"frames": ['
                        '{"best_effort_timestamp_time": "2.000000"},'
                        '{"best_effort_timestamp_time": "2.017000"},'
                        '{"best_effort_timestamp_time": "2.050000"}]}' )
        result = measure(pts, 1, 3)
        self.assertEqual(result["elapsed_ms"], Decimal("50.000000"))
        self.assertEqual(result["start_pts_ms"], Decimal("2000.000000"))

    def test_missing_or_nonmonotonic_pts_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "frame 2"):
            parse_pts('{"frames": [{"best_effort_timestamp_time": "0"}, {}]}')
        with self.assertRaisesRegex(ValueError, "frame 2"):
            parse_pts('{"frames": [{"best_effort_timestamp_time": "0.02"},'
                      '{"best_effort_timestamp_time": "0.01"}]}')

    def test_frame_bounds_are_one_based(self):
        with self.assertRaisesRegex(ValueError, "1 <="):
            measure([Decimal("0"), Decimal("0.01")], 0, 2)

    def test_numbered_images_must_match_pts_count(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "000001.png").touch()
            (folder / "000002.png").touch()
            validate_numbered_frames(folder, 2)
            with self.assertRaisesRegex(ValueError, "do not match"):
                validate_numbered_frames(folder, 3)


if __name__ == "__main__":
    unittest.main()
