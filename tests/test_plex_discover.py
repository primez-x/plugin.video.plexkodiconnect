import unittest
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from resources.lib import plex_discover


class PlexDiscoverTests(unittest.TestCase):
    MOVIE_KEY = "5d7768244de0ee001fcc7fed"
    SHOW_KEY = "6244890dd4640db32655f36b"

    def test_tmdb_match_parameters_use_exact_id_and_media_type(self):
        self.assertEqual(
            plex_discover.tmdb_match_parameters("603", "movie"),
            {"guid": "tmdb://603", "type": 1},
        )
        self.assertEqual(
            plex_discover.tmdb_match_parameters("132159", "tvshow"),
            {"guid": "tmdb://132159", "type": 2},
        )
        self.assertIsNone(plex_discover.tmdb_match_parameters("603x", "movie"))
        self.assertIsNone(plex_discover.tmdb_match_parameters("603", "episode"))

    def test_resolves_a_single_canonical_match_without_title_or_year(self):
        payload = {
            "MediaContainer": {
                "Metadata": {
                    "type": "movie",
                    "guid": "plex://movie/%s" % self.MOVIE_KEY,
                    "ratingKey": self.MOVIE_KEY,
                }
            }
        }

        self.assertEqual(
            plex_discover.resolve_tmdb_match(payload, "movie"),
            {
                "guid": "plex://movie/%s" % self.MOVIE_KEY,
                "rating_key": self.MOVIE_KEY,
            },
        )

    def test_resolves_a_single_canonical_tvshow_match(self):
        payload = {
            "MediaContainer": {
                "Metadata": {
                    "type": 2,
                    "guid": "plex://show/%s" % self.SHOW_KEY,
                    "ratingKey": self.SHOW_KEY,
                }
            }
        }

        self.assertEqual(
            plex_discover.resolve_tmdb_match(payload, "tvshow"),
            {
                "guid": "plex://show/%s" % self.SHOW_KEY,
                "rating_key": self.SHOW_KEY,
            },
        )

    def test_rejects_ambiguous_wrong_type_and_noncanonical_matches(self):
        valid_movie = {
            "type": 1,
            "guid": "plex://movie/%s" % self.MOVIE_KEY,
            "ratingKey": self.MOVIE_KEY,
        }
        valid_show = {
            "type": "show",
            "guid": "plex://show/%s" % self.SHOW_KEY,
            "ratingKey": self.SHOW_KEY,
        }

        with self.subTest("ambiguous"):
            payload = {
                "MediaContainer": {
                    "Metadata": [
                        valid_movie,
                        {
                            "type": "movie",
                            "guid": "plex://movie/60a0da3517d287002c2469ff",
                            "ratingKey": "60a0da3517d287002c2469ff",
                        },
                    ]
                }
            }
            self.assertIsNone(plex_discover.resolve_tmdb_match(payload, "movie"))

        with self.subTest("wrong type"):
            self.assertIsNone(
                plex_discover.resolve_tmdb_match(
                    {"MediaContainer": {"Metadata": valid_show}},
                    "movie",
                )
            )

        with self.subTest("rating key does not match canonical guid"):
            mismatched = dict(valid_movie, ratingKey="unexpected")
            self.assertIsNone(
                plex_discover.resolve_tmdb_match(
                    {"MediaContainer": {"Metadata": mismatched}},
                    "movie",
                )
            )

        with self.subTest("malformed guid"):
            malformed = dict(valid_movie, guid="plex://movie/key/extra")
            self.assertIsNone(
                plex_discover.resolve_tmdb_match(
                    {"MediaContainer": {"Metadata": malformed}},
                    "movie",
                )
            )


if __name__ == "__main__":
    unittest.main()
