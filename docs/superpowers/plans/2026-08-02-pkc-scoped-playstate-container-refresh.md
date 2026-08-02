# PKC Scoped Playstate Container Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PKC's playback-time full-skin reload with a shared, targeted invalidation of opted-in nested episode containers.

**Architecture:** A new `resources.lib.gui_refresh` module owns the skin-neutral `PKC.SidePanelContainers`/`PKC.SyncToken` contract. Library sync and playback monitoring call the same helper; playback always refreshes the active container after a successful database write and additionally invalidates relevant episode panels without ever calling `ReloadSkin()`.

**Tech Stack:** Python 3, Kodi Python API (`xbmc`), PKC `PlexDB`, standard-library `uuid`, `unittest`, PowerShell, Kodi JSON-RPC, SMB/SSH for CoreELEC verification.

## Global Constraints

- Do not change Plex/Kodi watched-threshold, Up Next, or handoff semantics.
- Do not add an Arctic Fuse dependency or edit Arctic Fuse.
- Run nested invalidation for both completed and partial-resume episode writes.
- Keep current-listing `Container.Refresh`; remove playback-time `ReloadSkin()` completely.
- Skip malformed opt-in declarations without interrupting playback cleanup.
- Fail open for declared TV paths whose show ID cannot be parsed safely.
- Use a UUID hex token so two invalidations cannot reuse a one-second timestamp.
- Preserve and never stage the pre-existing `resources/lib/transfer.py` modification.
- Publish from `python3-beta` as PKC `3.12.47` only after automated and live verification.

---

### Task 1: Shared nested-container invalidation

**Files:**
- Create: `resources/lib/gui_refresh.py`
- Create: `tests/test_gui_refresh.py`
- Modify: `resources/lib/library_sync/common.py:3-135`

**Interfaces:**
- Consumes: `changed_items`, an iterable of `(plex_id, plex_type)` pairs; `Window(10000).Property(PKC.SidePanelContainers)`; `Container(<id>).FolderPath`; `PlexDB.episode(plex_id)` returning a mapping with Kodi `grandparent_id`.
- Produces: `refresh_sidepanel_containers(changed_items) -> None`, which issues `SetProperty(PKC.SyncToken.<id>,<uuid_hex>,10000)` only for relevant opted-in TV containers and never propagates UI-refresh failures.

- [x] **Step 1: Write behavior-first invalidation tests**

Create `tests/test_gui_refresh.py` with this loader so the real module executes
against controlled `xbmc`, `variables`, and `PlexDB` boundaries:

```python
import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_gui_refresh(labels, episodes=None, episode_error=None):
    for module_name in (
            'xbmc', 'resources.lib.gui_refresh',
            'resources.lib.plex_db', 'resources.lib.variables'):
        sys.modules.pop(module_name, None)
    lib_package = sys.modules.get('resources.lib')
    if lib_package is not None:
        for name in ('gui_refresh', 'plex_db', 'variables'):
            lib_package.__dict__.pop(name, None)

    xbmc = types.ModuleType('xbmc')
    xbmc.commands = []
    xbmc.getInfoLabel = lambda label: labels.get(label, '')
    xbmc.executebuiltin = xbmc.commands.append

    plex_db = types.ModuleType('resources.lib.plex_db')
    plex_db.episode_calls = []
    plex_db.open_count = 0
    episodes = {} if episodes is None else episodes

    class FakePlexDB(object):
        def __init__(self, lock=False):
            plex_db.open_count += 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def episode(self, plex_id):
            plex_db.episode_calls.append(plex_id)
            if episode_error is not None:
                raise episode_error
            return episodes.get(plex_id)

    plex_db.PlexDB = FakePlexDB
    variables = types.ModuleType('resources.lib.variables')
    variables.PLEX_TYPE_EPISODE = 'episode'
    sys.modules['xbmc'] = xbmc
    sys.modules['resources.lib.plex_db'] = plex_db
    sys.modules['resources.lib.variables'] = variables
    gui_refresh = importlib.import_module('resources.lib.gui_refresh')
    return gui_refresh, xbmc, plex_db


class GuiRefreshTests(unittest.TestCase):
    def test_refreshes_only_affected_show_for_titles_and_genres_paths(self):
        gui_refresh, xbmc, plex_db = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)':
                '530|bad|531|532|533',
            'Container(530).FolderPath':
                'videodb://tvshows/titles/160/?season=1',
            'Container(531).FolderPath':
                'videodb://tvshows/genres/22/160/?season=1',
            'Container(532).FolderPath':
                'videodb://tvshows/genres/22/999/?season=1',
            'Container(533).FolderPath':
                'videodb://movies/titles/160/',
        }, {'16388': {'grandparent_id': 160}})
        gui_refresh.uuid4 = lambda: SimpleNamespace(hex='unique-token')

        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))

        self.assertEqual(xbmc.commands, [
            'SetProperty(PKC.SyncToken.530,unique-token,10000)',
            'SetProperty(PKC.SyncToken.531,unique-token,10000)',
        ])
        self.assertEqual(plex_db.episode_calls, ['16388'])

    def test_unparseable_tv_path_refreshes_fail_open(self):
        gui_refresh, xbmc, _ = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)': '530',
            'Container(530).FolderPath':
                'videodb://tvshows/studios/ABC/160/?season=1',
        }, {'16388': {'grandparent_id': 999}})
        gui_refresh.uuid4 = lambda: SimpleNamespace(hex='fail-open')

        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))

        self.assertEqual(xbmc.commands, [
            'SetProperty(PKC.SyncToken.530,fail-open,10000)',
        ])

    def test_successive_invalidations_use_distinct_tokens(self):
        gui_refresh, xbmc, _ = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)': '530',
            'Container(530).FolderPath':
                'videodb://tvshows/titles/160/',
        }, {'16388': {'grandparent_id': 160}})
        tokens = iter(('token-one', 'token-two'))
        gui_refresh.uuid4 = lambda: SimpleNamespace(hex=next(tokens))

        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))
        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))

        self.assertEqual(xbmc.commands, [
            'SetProperty(PKC.SyncToken.530,token-one,10000)',
            'SetProperty(PKC.SyncToken.530,token-two,10000)',
        ])

    def test_non_episode_change_does_not_open_plex_db_or_signal_panels(self):
        gui_refresh, xbmc, plex_db = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)': '530',
            'Container(530).FolderPath':
                'videodb://tvshows/titles/160/',
        })

        gui_refresh.refresh_sidepanel_containers((('500', 'movie'),))

        self.assertEqual(xbmc.commands, [])
        self.assertEqual(plex_db.open_count, 0)

    def test_lookup_failure_refreshes_declared_tv_containers_without_raising(self):
        gui_refresh, xbmc, _ = load_gui_refresh({
            'Window(10000).Property(PKC.SidePanelContainers)': '530|531|532',
            'Container(530).FolderPath':
                'videodb://tvshows/titles/160/',
            'Container(531).FolderPath':
                'videodb://tvshows/genres/22/999/',
            'Container(532).FolderPath':
                'videodb://movies/titles/160/',
        }, episode_error=RuntimeError('database unavailable'))
        gui_refresh.LOG.exception = lambda *args, **kwargs: None
        gui_refresh.uuid4 = lambda: SimpleNamespace(hex='db-fail-open')

        gui_refresh.refresh_sidepanel_containers((('16388', 'episode'),))

        self.assertEqual(xbmc.commands, [
            'SetProperty(PKC.SyncToken.530,db-fail-open,10000)',
            'SetProperty(PKC.SyncToken.531,db-fail-open,10000)',
        ])
```

- [x] **Step 2: Run the new tests and verify RED**

Run: `python -m unittest tests.test_gui_refresh -v`

Expected: import failure for missing `resources.lib.gui_refresh`. This proves the tests exercise the new production boundary.

- [x] **Step 3: Implement the shared helper**

Create `resources/lib/gui_refresh.py` with the following production structure:

```python
from logging import getLogger
import re
from uuid import uuid4
import xbmc

from .plex_db import PlexDB
from . import variables as v

LOG = getLogger('PLEX.gui_refresh')
CONTAINERS_LABEL = 'Window(10000).Property(PKC.SidePanelContainers)'
TVSHOW_PATHS = (
    re.compile(r'^videodb://tvshows/titles/(\d+)(?:/|\?|$)'),
    re.compile(r'^videodb://tvshows/genres/\d+/(\d+)(?:/|\?|$)'),
)


def refresh_sidepanel_containers(changed_items):
    try:
        episode_ids = tuple(
            plex_id for plex_id, plex_type in changed_items
            if plex_type == v.PLEX_TYPE_EPISODE)
        if not episode_ids:
            return
        container_ids = tuple(_declared_container_ids())
        if not container_ids:
            return
        affected_show_ids = _affected_show_ids(episode_ids)
        token = uuid4().hex
        for container_id in container_ids:
            _refresh_container(container_id, affected_show_ids, token)
    except Exception:
        LOG.exception('Could not refresh side-panel containers')


def _declared_container_ids():
    seen = set()
    value = xbmc.getInfoLabel(CONTAINERS_LABEL)
    for container_id in value.split('|'):
        try:
            container_id = int(container_id.strip())
        except ValueError:
            continue
        if container_id in seen:
            continue
        seen.add(container_id)
        yield container_id


def _tvshow_id(folder_path):
    for pattern in TVSHOW_PATHS:
        match = pattern.match(folder_path)
        if match:
            return int(match.group(1))


def _affected_show_ids(episode_ids):
    show_ids = set()
    try:
        with PlexDB(lock=False) as plexdb:
            for plex_id in episode_ids:
                episode = plexdb.episode(plex_id)
                if not episode:
                    continue
                try:
                    show_ids.add(int(episode.get('grandparent_id')))
                except (TypeError, ValueError):
                    continue
    except Exception:
        LOG.exception('Could not resolve changed episodes to TV shows')
        return None
    return show_ids


def _refresh_container(container_id, affected_show_ids, token):
    try:
        folder_path = xbmc.getInfoLabel(
            'Container(%d).FolderPath' % container_id)
        if not folder_path or not folder_path.startswith(
                'videodb://tvshows/'):
            return
        show_id = _tvshow_id(folder_path)
        if affected_show_ids is not None \
                and show_id is not None \
                and show_id not in affected_show_ids:
            return
        xbmc.executebuiltin(
            'SetProperty(PKC.SyncToken.%d,%s,10000)'
            % (container_id, token))
        LOG.info('Signaled side-panel container %d reload: %s',
                 container_id, folder_path)
    except Exception:
        LOG.exception('Could not refresh side-panel container %d',
                      container_id)
```

- [x] **Step 4: Route library sync through the shared helper**

In `resources/lib/library_sync/common.py`, import `gui_refresh`, replace the private call with:

```python
if changed_items:
    gui_refresh.refresh_sidepanel_containers(changed_items)
```

Delete `_refresh_sidepanel_containers` and `_is_sidepanel_relevant`; no compatibility wrapper remains because both callers are internal and the new public module is the single owner.

- [x] **Step 5: Run focused tests and verify GREEN**

Run: `python -m unittest tests.test_gui_refresh -v`

Expected: all GUI-refresh tests pass, including exact targeted commands, one database lookup per changed episode, fail-open behavior, and distinct UUID tokens.

### Task 2: Playback integration and legacy full-skin branch removal

**Files:**
- Modify: `resources/lib/kodimonitor.py:15-20,525-635`
- Modify: `tests/test_kodimonitor.py:14-145,283`

**Interfaces:**
- Consumes: `gui_refresh.refresh_sidepanel_containers(((plex_id, plex_type),))` after successful Kodi database writes.
- Produces: `_playback_progress(status, ended, db_item) -> (resume_seconds, total_seconds, playcount, last_played)`; `_record_playstate` always runs `Container.Refresh` and signals nested panels only for episodes.

- [x] **Step 1: Extend the real playback-stop harness**

Update `load_kodimonitor()` to register a fake `resources.lib.gui_refresh` with a `calls` list, add `PLEX_TYPE_EPISODE = 'episode'`, implement the existing `FunctionAsTask`/`addTasksToFront` interfaces used by `_record_playstate`, and provide deterministic timing conversions. Keep the real `kodimonitor._record_playstate` and `_playback_progress` code under test.

Use these exact additions in the loader:

```python
timing.kodi_time_to_millis = lambda value: (
    (value['hours'] * 3600 + value['minutes'] * 60 + value['seconds'])
    * 1000 + value['milliseconds'])
timing.kodi_now = lambda: '2026-08-02 09:00:00'

gui_refresh = types.ModuleType('resources.lib.gui_refresh')
gui_refresh.calls = []
gui_refresh.refresh_sidepanel_containers = \
    lambda items: gui_refresh.calls.append(tuple(items))

class FunctionAsTask(Task):
    def __init__(self, function, callback, *args, **kwargs):
        super(FunctionAsTask, self).__init__()
        self.function = function
        self.callback = callback
        self.args = args
        self.kwargs = kwargs

@classmethod
def addTasksToFront(cls, tasks):
    cls.tasks.extend(tasks)

backgroundthread.FunctionAsTask = FunctionAsTask
backgroundthread.BGThreader.addTasksToFront = addTasksToFront

variables.PLEX_TYPE_EPISODE = 'episode'
variables.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR = 0
variables.MARK_PLAYED_AT = 0.90
variables.KODI_PLAYCOUNTMINIMUMPERCENT = 0.80
variables.KODI_IGNORESECONDSATSTART = 10
variables.KODI_IGNOREPERCENTATEND = 8
variables.IGNORE_SECONDS_AT_START = 5
```

Include `resources.lib.gui_refresh` in the loader's cache-removal tuple and
module mapping so `kodimonitor.py` imports this fake while all playback logic
remains real.

Add these test helpers below `load_kodimonitor()`:

```python
def kodi_time(seconds):
    milliseconds = int(seconds * 1000)
    return {
        'hours': milliseconds // 3600000,
        'minutes': milliseconds // 60000 % 60,
        'seconds': milliseconds // 1000 % 60,
        'milliseconds': milliseconds % 1000,
    }


def playback_status(plex_type='episode', position_seconds=1020,
                    total_seconds=1200):
    return {
        'plex_id': '16388',
        'plex_type': plex_type,
        'playcount': 0,
        'external_player': False,
        'time': kodi_time(position_seconds),
        'totaltime': kodi_time(total_seconds),
        'first_credits_marker': None,
        'final_credits_marker': None,
    }


def install_playstate_databases(kodimonitor):
    writes = []

    class FakePlexDB(object):
        def __init__(self, lock=False):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def item_by_id(self, plex_id, plex_type):
            return {'plex_id': plex_id, 'kodi_fileid': 42}

    class FakeKodiVideoDB(object):
        def __init__(self, lock=True):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def set_resume(self, *args):
            writes.append(args)

    kodimonitor.PlexDB = FakePlexDB
    kodimonitor.kodi_db.KodiVideoDB = FakeKodiVideoDB
    return writes
```

- [x] **Step 2: Write failing completed and partial playback tests**

Add two integration tests using context-manager fakes for `PlexDB` and `KodiVideoDB`:

```python
def test_completed_episode_write_refreshes_outer_and_nested_containers(self):
    kodimonitor, xbmc, _, _ = load_kodimonitor()
    writes = install_playstate_databases(kodimonitor)
    kodimonitor._playback_progress = lambda status, ended, db_item: (
        0.0, 1434.96, 1, '2026-08-02 08:53:51')

    kodimonitor._record_playstate(playback_status(), ended=True)

    self.assertEqual(writes, [
        (42, 0.0, 1434.96, 1, '2026-08-02 08:53:51'),
    ])
    self.assertEqual(xbmc.commands, ['Container.Refresh'])
    self.assertEqual(kodimonitor.gui_refresh.calls,
                     [(('16388', 'episode'),)])

def test_partial_episode_threshold_mismatch_uses_scoped_refresh(self):
    kodimonitor, xbmc, _, _ = load_kodimonitor()
    writes = install_playstate_databases(kodimonitor)
    kodimonitor.v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR = 0
    kodimonitor.v.MARK_PLAYED_AT = 0.90
    kodimonitor.v.KODI_PLAYCOUNTMINIMUMPERCENT = 0.80
    kodimonitor.v.KODI_IGNORESECONDSATSTART = 10
    kodimonitor.v.KODI_IGNOREPERCENTATEND = 8
    kodimonitor.v.IGNORE_SECONDS_AT_START = 5

    kodimonitor._record_playstate(
        playback_status(position_seconds=1020, total_seconds=1200),
        ended=False)

    self.assertEqual(writes, [
        (42, 1020.0, 1200.0, 0, '2026-08-02 09:00:00'),
    ])
    self.assertEqual(xbmc.commands, ['Container.Refresh'])
    self.assertEqual(kodimonitor.gui_refresh.calls,
                     [(('16388', 'episode'),)])

def test_movie_write_refreshes_only_outer_container(self):
    kodimonitor, xbmc, _, _ = load_kodimonitor()
    install_playstate_databases(kodimonitor)
    kodimonitor._playback_progress = lambda status, ended, db_item: (
        300.0, 7200.0, 0, '2026-08-02 09:00:00')

    kodimonitor._record_playstate(
        playback_status(plex_type='movie'), ended=False)

    self.assertEqual(xbmc.commands, ['Container.Refresh'])
    self.assertEqual(kodimonitor.gui_refresh.calls, [])
```

Add a movie case asserting `['Container.Refresh']` and no nested calls. The partial case intentionally exercises the legacy branch that currently emits `ReloadSkin()`.

- [x] **Step 3: Run the playback tests and verify RED**

Run: `python -m unittest tests.test_kodimonitor.KodiMonitorTests.test_completed_episode_write_refreshes_outer_and_nested_containers tests.test_kodimonitor.KodiMonitorTests.test_partial_episode_threshold_mismatch_uses_scoped_refresh tests.test_kodimonitor.KodiMonitorTests.test_movie_write_refreshes_only_outer_container -v`

Expected: failures because playback does not call `gui_refresh`, and the partial mismatch produces `ReloadSkin()`.

- [x] **Step 4: Implement the scoped playback flow**

Import `gui_refresh` in `kodimonitor.py`. Change `_record_playstate` to unpack four values, preserve both `set_resume` writes, then execute:

```python
xbmc.executebuiltin('Container.Refresh')
if status['plex_type'] == v.PLEX_TYPE_EPISODE:
    gui_refresh.refresh_sidepanel_containers((
        (status['plex_id'], status['plex_type']),
    ))
```

Remove `reload_skin` initialization, both Kodi/PKC threshold-mismatch assignments, the force-reload debug log, and the fifth return value from `_playback_progress`. Do not alter its watched-marker, resume, playcount, ignore-start, or external-player decisions.

- [x] **Step 5: Run focused playback tests and verify GREEN**

Run: `python -m unittest tests.test_kodimonitor -v`

Expected: all `test_kodimonitor` tests pass; completed and partial episodes use the same scoped flow, movies do not invalidate episode panels, and no command is `ReloadSkin()`.

### Task 3: Release and end-to-end verification

**Files:**
- Modify: `addon.xml:2`
- Verify: `docs/superpowers/specs/2026-08-02-pkc-scoped-playstate-container-refresh-design.md`
- Verify: `docs/superpowers/plans/2026-08-02-pkc-scoped-playstate-container-refresh.md`

**Interfaces:**
- Consumes: verified Task 1 and Task 2 behavior.
- Produces: PKC `3.12.47` committed and pushed on `python3-beta`, installed on the living-room CoreELEC device, with the published repository artifact verified.

- [x] **Step 1: Run the complete automated verification**

Run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q resources tests
rg -n 'ReloadSkin\(\)|reload_skin' resources/lib/kodimonitor.py
& 'C:\Program Files\Git\cmd\git.exe' diff --check
```

Expected: 0 test failures, compile success, no `ReloadSkin()`/`reload_skin` matches in `kodimonitor.py`, and no whitespace errors.

- [x] **Step 2: Bump the published add-on version**

Change root `addon.xml` from `version="3.12.46"` to `version="3.12.47"`, then rerun the complete automated verification so the release metadata is part of the tested tree.

- [x] **Step 3: Deploy the candidate to the living-room device**

Confirm JSON-RPC reports no active player, disable the PKC add-on service, copy only the changed runtime files and `addon.xml` to the installed `plugin.video.plexkodiconnect` directory over the established SMB connection, re-enable PKC, and confirm `Addons.GetAddonDetails` reports enabled version `3.12.47`. Do not restart or modify the offline basement device.

- [x] **Step 4: Verify the scoped stop refresh live**

In Arctic Fuse's Combined seasons view on `192.168.0.131`, capture the declared nested-container folder paths and sync tokens, then perform one completed stop and one partial-resume stop. Verify through JSON-RPC and Plex metadata that playcount/resume state is correct; verify the relevant `PKC.SyncToken.<id>` changes, the nested listitem updates without leaving the view, and the log contains no skin reload from the playback path.

Execution safety deviation: completed and partial state transitions remain
covered by the RED/GREEN integration tests. Live verification used a short
stop on an already watched episode to exercise the same post-write refresh
path without intentionally changing watch history. That exposed a separate,
pre-existing short-playback reset; the test-created Kodi and Plex state was
restored exactly before proceeding. The scoped refresh changed all seven
declared show-160 tokens, rebuilt the episode panel, and retained the active
Videos/Season 1 window.

- [ ] **Step 5: Review the exact diff and commit once**

Run:

```powershell
& 'C:\Program Files\Git\cmd\git.exe' diff -- resources/lib/gui_refresh.py resources/lib/library_sync/common.py resources/lib/kodimonitor.py tests/test_gui_refresh.py tests/test_kodimonitor.py addon.xml docs/superpowers
& 'C:\Program Files\Git\cmd\git.exe' status --short
```

Stage only those task files, confirm `resources/lib/transfer.py` remains unstaged, and commit once with:

```powershell
& 'C:\Program Files\Git\cmd\git.exe' commit -m 'Refresh nested episode state after playback'
```

One release commit avoids artificial version bumps for intermediate TDD checkpoints on the published branch.

- [ ] **Step 6: Push and verify the published artifact**

Push `python3-beta`, verify the central repository workflow completes, then confirm live `addons.xml` contains `3.12.47`, the raw-byte MD5 matches `addons.xml.md5`, the `plugin.video.plexkodiconnect-3.12.47.zip` URL returns HTTP 200, and its source manifest identifies the pushed commit. Refresh the CoreELEC repository and confirm the installed add-on remains enabled at `3.12.47`.
