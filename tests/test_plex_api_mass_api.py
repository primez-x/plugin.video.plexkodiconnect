import importlib.util
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class FakeLibrary(object):
    """The PKC Plex DB and Kodi video DB of one synced library."""

    def __init__(self, by_guid=None, by_kodi_id=None, uniqueids=None):
        self.by_guid = by_guid or {}
        # {(kodi_type, kodi_id): plex_id} of items synced by PKC
        self.by_kodi_id = by_kodi_id or {}
        # [(kodi_id, kodi_type, provider, value)] of Kodi's uniqueid table
        self.uniqueids = uniqueids or []
        self.kodi_connections = 0
        self.kodi_queries = []
        self.kodi_error = None


def load_plex_api(library):
    package = sys.modules.get("resources.lib")

    class Base(object):
        def __init__(self, xml):
            self.xml = xml
            self.kodi_id = None

        @property
        def plex_type(self):
            return self.xml.get("type")

        @property
        def plex_id(self):
            return self.xml.get("ratingKey")

        @property
        def guids(self):
            # Mirrors Base._scan_children for <Guid id="tmdb://603"/>
            guids = {}
            for child in self.xml:
                if child.tag == "Guid":
                    guid = child.get("id").split("://", 1)
                    guids[guid[0]] = guid[1]
            return guids

        def check_by_guid(self, plexdb=None):
            return plexdb.items_by_guid(self.xml.get("guid"), self.plex_type)

        def check_db(self, plexdb=None):
            self.kodi_id = plexdb.kodi_id(self.plex_id)

    base = types.ModuleType("resources.lib.plex_api.base")
    base.Base = Base
    sys.modules["resources.lib.plex_api.base"] = base
    for name, cls in (
        ("artwork", "Artwork"),
        ("file", "File"),
        ("media", "Media"),
        ("user", "User"),
        ("playback", "Playback"),
    ):
        module = types.ModuleType("resources.lib.plex_api.%s" % name)
        setattr(module, cls, type(cls, (object,), {}))
        sys.modules["resources.lib.plex_api.%s" % name] = module

    class PlexDB(object):
        def __init__(self, lock=False):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @staticmethod
        def items_by_guid(guid, plex_type=None):
            return [{"plex_id": plex_id} for plex_id in library.by_guid.get(guid, ())]

        @staticmethod
        def item_by_kodi_id(kodi_id, kodi_type):
            plex_id = library.by_kodi_id.get((kodi_type, kodi_id))
            return {"plex_id": plex_id} if plex_id is not None else None

        @staticmethod
        def kodi_id(plex_id):
            for (_, kodi_id), synced_plex_id in library.by_kodi_id.items():
                if str(synced_plex_id) == str(plex_id):
                    return kodi_id
            return None

    plex_db = types.ModuleType("resources.lib.plex_db")
    plex_db.PlexDB = PlexDB
    sys.modules["resources.lib.plex_db"] = plex_db

    class KodiVideoDB(object):
        def __init__(self, lock=True):
            pass

        def __enter__(self):
            library.kodi_connections += 1
            if library.kodi_error is not None:
                raise library.kodi_error
            return self

        def __exit__(self, *args):
            return False

        @staticmethod
        def kodi_ids_by_uniqueids(kodi_type, values):
            library.kodi_queries.append((kodi_type, sorted(values)))
            result = {}
            for kodi_id, media_type, provider, value in library.uniqueids:
                if media_type == kodi_type and value in values:
                    result.setdefault((provider, value), []).append(kodi_id)
            return result

    kodi_db = types.ModuleType("resources.lib.kodi_db")
    kodi_db.KodiVideoDB = KodiVideoDB
    sys.modules["resources.lib.kodi_db"] = kodi_db

    variables = types.ModuleType("resources.lib.variables")
    variables.PLEX_TYPE_MOVIE = "movie"
    variables.PLEX_TYPE_SHOW = "show"
    variables.KODI_TYPE_MOVIE = "movie"
    variables.KODI_TYPE_SHOW = "tvshow"
    sys.modules["resources.lib.variables"] = variables
    if package is not None:
        # "from .. import variables" prefers the package attribute
        package.variables = variables
        package.kodi_db = kodi_db

    path = REPO_ROOT / "resources" / "lib" / "plex_api" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "resources.lib.plex_api",
        path,
        submodule_search_locations=[str(path.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["resources.lib.plex_api"] = module
    spec.loader.exec_module(module)
    return module


def provider_item(parent, rating_key, plex_type="movie", **guids):
    item = ET.SubElement(
        parent,
        "Video" if plex_type == "movie" else "Directory",
        type=plex_type,
        ratingKey=rating_key,
        guid="plex://%s/%s" % (plex_type, rating_key),
    )
    for provider, value in guids.items():
        ET.SubElement(item, "Guid", id="%s://%s" % (provider, value))
    return item


class MassApiExternalIdTests(unittest.TestCase):
    def setUp(self):
        import resources.lib  # noqa: F401  (the package the stubs live in)

        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        package = sys.modules["resources.lib"]
        attributes = dict(vars(package))

        def restore():
            for name in list(vars(package)):
                if name not in attributes:
                    delattr(package, name)
            for name, value in attributes.items():
                setattr(package, name, value)

        self.addCleanup(restore)

    def test_legacy_agent_item_matches_by_tmdb_like_an_exact_guid_match(self):
        library = FakeLibrary(
            by_kodi_id={("movie", 7): 41},
            uniqueids=[(7, "movie", "tmdb", "603"), (7, "movie", "imdb", "tt0133093")],
        )
        plex_api = load_plex_api(library)
        xml = ET.Element("MediaContainer")
        provider_item(xml, "abc", tmdb="603", imdb="tt0133093")

        apis = plex_api.mass_api(xml, check_by_guid=True, match_external_ids=True)

        self.assertEqual(len(apis), 1)
        self.assertEqual(apis[0].xml.get("ratingKey"), "41")
        self.assertEqual(apis[0].xml.get("key"), "/library/metadata/41")
        self.assertEqual(apis[0].kodi_id, 7)
        # The provider guid stays for the caller's guid lookup
        self.assertEqual(apis[0].xml.get("guid"), "plex://movie/abc")

    def test_external_ids_are_only_matched_on_request(self):
        library = FakeLibrary(
            by_kodi_id={("movie", 7): 41},
            uniqueids=[(7, "movie", "tmdb", "603")],
        )
        plex_api = load_plex_api(library)
        xml = ET.Element("MediaContainer")
        provider_item(xml, "abc", tmdb="603")

        self.assertEqual(plex_api.mass_api(xml, check_by_guid=True), [])
        self.assertEqual(library.kodi_connections, 0)

    def test_tmdb_is_preferred_then_imdb_then_tvdb(self):
        library = FakeLibrary(
            by_kodi_id={
                ("movie", 7): 41,
                ("movie", 8): 50,
                ("tvshow", 3): 60,
                ("tvshow", 4): 70,
            },
            uniqueids=[
                (7, "movie", "tmdb", "603"),
                (8, "movie", "imdb", "tt0133093"),
                (8, "movie", "imdb", "tt0234215"),
                (3, "tvshow", "tvdb", "81189"),
                (4, "tvshow", "imdb", "tt0903747"),
            ],
        )
        plex_api = load_plex_api(library)
        xml = ET.Element("MediaContainer")
        provider_item(xml, "matrix", tmdb="603", imdb="tt0133093")
        provider_item(xml, "reloaded", tmdb="604", imdb="tt0234215")
        provider_item(xml, "breaking-bad", "show", tmdb="1396", imdb="tt0903747", tvdb="81189")
        provider_item(xml, "tvdb-only", "show", tmdb="9", imdb="tt9", tvdb="81189")

        apis = plex_api.mass_api(xml, check_by_guid=True, match_external_ids=True)

        self.assertEqual(
            [api.xml.get("ratingKey") for api in apis], ["41", "50", "70", "60"]
        )

    def test_media_type_must_match(self):
        library = FakeLibrary(
            by_kodi_id={("movie", 7): 41},
            # TMDb movie and TV ids are separate namespaces
            uniqueids=[(7, "movie", "tmdb", "1396")],
        )
        plex_api = load_plex_api(library)
        xml = ET.Element("MediaContainer")
        provider_item(xml, "show", "show", tmdb="1396")

        self.assertEqual(
            plex_api.mass_api(xml, check_by_guid=True, match_external_ids=True), []
        )
        self.assertEqual(library.kodi_queries, [("tvshow", ["1396"])])

    def test_kodi_items_not_synced_by_pkc_are_not_matched(self):
        library = FakeLibrary(uniqueids=[(7, "movie", "tmdb", "603")])
        plex_api = load_plex_api(library)
        xml = ET.Element("MediaContainer")
        provider_item(xml, "abc", tmdb="603")

        self.assertEqual(
            plex_api.mass_api(xml, check_by_guid=True, match_external_ids=True), []
        )

    def test_every_matching_library_copy_gets_its_own_api(self):
        library = FakeLibrary(
            by_kodi_id={("movie", 7): 41, ("movie", 9): 42},
            uniqueids=[(9, "movie", "tmdb", "603"), (7, "movie", "tmdb", "603")],
        )
        plex_api = load_plex_api(library)
        xml = ET.Element("MediaContainer")
        provider_item(xml, "abc", tmdb="603")

        apis = plex_api.mass_api(xml, check_by_guid=True, match_external_ids=True)

        self.assertEqual([api.xml.get("ratingKey") for api in apis], ["41", "42"])
        self.assertIsNot(apis[0].xml, apis[1].xml)

    def test_one_kodi_connection_serves_the_whole_listing(self):
        library = FakeLibrary(
            by_guid={"plex://movie/exact": [10]},
            by_kodi_id={("movie", 1): 10, ("movie", 7): 41, ("tvshow", 3): 60},
            uniqueids=[(7, "movie", "tmdb", "603"), (3, "tvshow", "tvdb", "81189")],
        )
        plex_api = load_plex_api(library)
        xml = ET.Element("MediaContainer")
        provider_item(xml, "exact", tmdb="1")
        provider_item(xml, "abc", tmdb="603")
        provider_item(xml, "def", imdb="tt1")
        provider_item(xml, "show", "show", tvdb="81189")
        provider_item(xml, "no-ids")

        apis = plex_api.mass_api(xml, check_by_guid=True, match_external_ids=True)

        self.assertEqual(
            [api.xml.get("ratingKey") for api in apis], ["10", "41", "60"]
        )
        self.assertEqual(library.kodi_connections, 1)
        # The exact guid match is not looked up again
        self.assertEqual(
            library.kodi_queries,
            [("movie", ["603", "tt1"]), ("tvshow", ["81189"])],
        )

    def test_no_kodi_connection_without_provider_ids(self):
        library = FakeLibrary(by_guid={"plex://movie/exact": [10]})
        plex_api = load_plex_api(library)
        xml = ET.Element("MediaContainer")
        provider_item(xml, "exact", tmdb="603")
        provider_item(xml, "no-ids")
        ET.SubElement(xml, "Video", type="clip", ratingKey="clip", guid="plex://clip/c")

        apis = plex_api.mass_api(xml, check_by_guid=True, match_external_ids=True)

        self.assertEqual([api.xml.get("ratingKey") for api in apis], ["10"])
        self.assertEqual(library.kodi_connections, 0)

    def test_kodi_db_or_guid_errors_leave_items_provider_only(self):
        library = FakeLibrary(
            by_kodi_id={("movie", 7): 41},
            uniqueids=[(7, "movie", "tmdb", "603")],
        )
        plex_api = load_plex_api(library)
        xml = ET.Element("MediaContainer")
        ET.SubElement(provider_item(xml, "malformed"), "Guid", id="garbage")
        provider_item(xml, "abc", tmdb="603")

        apis = plex_api.mass_api(xml, check_by_guid=True, match_external_ids=True)
        self.assertEqual([api.xml.get("ratingKey") for api in apis], ["41"])

        library.kodi_error = RuntimeError("database is locked")
        xml = ET.Element("MediaContainer")
        provider_item(xml, "abc", tmdb="603")
        self.assertEqual(
            plex_api.mass_api(xml, check_by_guid=True, match_external_ids=True), []
        )


if __name__ == "__main__":
    unittest.main()
