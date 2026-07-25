import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from resources.lib import plex_discover


class PlexDiscoverTests(unittest.TestCase):
    def test_tmdb_match_parameters_only_accept_exact_numeric_id_and_video_type(self):
        self.assertEqual(
            plex_discover.tmdb_match_parameters(' 603 ', 'movie'),
            {'guid': 'tmdb://603', 'type': 1})
        self.assertEqual(
            plex_discover.tmdb_match_parameters('1399', 'tvshow'),
            {'guid': 'tmdb://1399', 'type': 2})
        self.assertIsNone(plex_discover.tmdb_match_parameters('movie-603', 'movie'))
        self.assertIsNone(plex_discover.tmdb_match_parameters('603', 'episode'))

    def test_search_extracts_only_external_canonical_movie_and_show_metadata(self):
        payload = {
            'MediaContainer': {
                'SearchResults': [
                    {
                        'id': 'internal',
                        'SearchResult': [
                            {'Metadata': {'guid': 'plex://movie/internal',
                                          'type': 'movie'}}
                        ]
                    },
                    {
                        'id': 'external',
                        'SearchResult': [
                            {'Metadata': {'guid': 'plex://movie/matrix',
                                          'type': 'movie',
                                          'title': 'The Matrix'}},
                            {'Metadata': {'guid': 'plex://show/matrix-show',
                                          'type': 'show',
                                          'title': 'Matrix'}},
                            {'Metadata': {'guid': 'plex://movie/matrix',
                                          'type': 'movie',
                                          'title': 'Duplicate'}},
                            {'Metadata': {'guid': 'plex://person/smith',
                                          'type': 'person'}}
                        ]
                    }
                ]
            }
        }

        metadata = plex_discover.discover_search_metadata(payload)

        self.assertEqual([item['guid'] for item in metadata], [
            'plex://movie/matrix',
            'plex://show/matrix-show'
        ])

    def test_home_hubs_skip_malformed_metadata_and_keep_hub_titles(self):
        payload = {
            'MediaContainer': {
                'Hub': [
                    {
                        'title': 'Trending',
                        'Metadata': [
                            {'guid': 'plex://movie/trending', 'type': 'movie'},
                            {'guid': 'plex://movie/malformed/path', 'type': 'movie'},
                            'not-a-record'
                        ]
                    },
                    {'title': 'Empty', 'Metadata': []}
                ]
            }
        }

        hubs = plex_discover.discover_home_hubs(payload)

        self.assertEqual(len(hubs), 1)
        self.assertEqual(hubs[0]['title'], 'Trending')
        self.assertEqual(hubs[0]['metadata'][0]['guid'], 'plex://movie/trending')

    def test_exact_match_requires_one_canonical_response_of_the_requested_type(self):
        payload = {
            'MediaContainer': {
                'Metadata': [
                    {'guid': 'plex://movie/the-matrix', 'type': 'movie'},
                    {'guid': 'plex://show/the-matrix', 'type': 'show'}
                ]
            }
        }

        self.assertEqual(
            plex_discover.resolve_tmdb_match(payload, 'movie'),
            {
                'guid': 'plex://movie/the-matrix',
                'rating_key': 'the-matrix',
                'type': 'movie'
            })
        self.assertEqual(
            plex_discover.resolve_tmdb_match(payload, 'tv'),
            {
                'guid': 'plex://show/the-matrix',
                'rating_key': 'the-matrix',
                'type': 'show'
            })

    def test_exact_match_fails_closed_for_ambiguous_wrong_or_malformed_records(self):
        ambiguous = {
            'MediaContainer': {
                'Metadata': [
                    {'guid': 'plex://movie/matrix-one', 'type': 'movie'},
                    {'guid': 'plex://movie/matrix-two', 'type': 'movie'}
                ]
            }
        }
        wrong_type = {
            'MediaContainer': {
                'Metadata': [{'guid': 'plex://show/matrix', 'type': 'show'}]
            }
        }
        malformed = {
            'MediaContainer': {
                'Metadata': [{'guid': 'plex://movie/matrix/extra', 'type': 'movie'}]
            }
        }

        self.assertIsNone(plex_discover.resolve_tmdb_match(ambiguous, 'movie'))
        self.assertIsNone(plex_discover.resolve_tmdb_match(wrong_type, 'movie'))
        self.assertIsNone(plex_discover.resolve_tmdb_match(malformed, 'movie'))
        self.assertIsNone(plex_discover.rating_key_from_guid('plex://movie/a/b'))


if __name__ == '__main__':
    unittest.main()
