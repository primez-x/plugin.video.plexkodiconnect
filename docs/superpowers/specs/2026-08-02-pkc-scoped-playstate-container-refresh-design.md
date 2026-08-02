# PKC Scoped Playstate Container Refresh Design

## Problem

PKC records episode watch state correctly in both the Kodi database and Plex,
but Arctic Fuse's nested episode containers can continue displaying a cached
pre-stop `CVideoInfoTag`. The visible result is an episode that appears partly
watched after PKC has already cleared its resume point and incremented its play
count.

The current playback path refreshes only Kodi's active outer container. It does
not notify nested dynamic containers. A separate library-sync path already has
an opt-in token contract that changes a nested container's content URL and
causes Kodi to rebuild that container, but playback updates do not use it.

The playback path also contains a legacy `ReloadSkin()` fallback when PKC and
Kodi's playstate thresholds disagree. Resetting the entire skin is much broader
than the state change and is unacceptable for this workflow.

## Evidence and Scope

- For Jujutsu Kaisen S01E07, both Plex and Kodi reported watched state and no
  resume offset after playback stopped, while the skin still showed partial
  progress.
- Playback continued for roughly 40 seconds after Up Next appeared, placing the
  final position past the configured 90 percent threshold. The earlier 89
  percent estimate incorrectly treated the Up Next timestamp as the stop time.
- S01E04 through S01E06 were already recorded watched in Plex on the prior
  evening; the later manual actions did not establish that PKC had scrambled
  their backend states.
- Arctic Fuse already declares nested episode container IDs through
  `PKC.SidePanelContainers` and includes per-container `PKC.SyncToken.<id>`
  values in their content URLs.
- This change belongs in PKC's shared GUI invalidation behavior. No Arctic Fuse
  change is required.

## Goals

1. Make a successful PKC playback-state write immediately visible in opted-in
   nested episode containers.
2. Refresh only the current listing and nested containers whose displayed show
   may contain the changed episode.
3. Cover both completed playback and partial-resume playback.
4. Remove the playback-time `ReloadSkin()` code path completely.
5. Reuse one shared nested-container invalidation implementation for playback
   updates and library sync.
6. Preserve graceful behavior for skins that do not implement the opt-in
   contract.

## Non-Goals

- Changing Plex or Kodi watched-threshold semantics.
- Changing Up Next cancellation or handoff behavior.
- Adding Arctic Fuse-specific imports or hard-coded skin checks to PKC.
- Refreshing arbitrary widgets or resetting the active window.
- Altering unrelated PKC transfer behavior or existing user work.

## Architecture

### Shared GUI Refresh Module

Move the nested-container invalidation code out of
`library_sync.common` into a small top-level PKC GUI refresh module. Both
library sync and playback monitoring will call this public helper. Keeping it
outside the `library_sync` package avoids coupling playback monitoring to the
sync package's eager imports and lifecycle.

The helper accepts changed items as `(plex_id, plex_type)` pairs and:

1. Reads `Window(10000).Property(PKC.SidePanelContainers)`.
2. Validates each pipe-delimited container ID independently.
3. Reads `Container(<id>).FolderPath` for each declared container.
4. Ignores empty and non-TV database paths.
5. Extracts the TV show ID from supported Kodi TV paths, including both
   `videodb://tvshows/titles/<show_id>/<remaining_path>` and the observed
   Combined-view form
   `videodb://tvshows/genres/<genre_id>/<show_id>/<remaining_path>`.
6. Resolves changed episodes to their Kodi TV show IDs through `PlexDB`.
7. Changes only the token of a container displaying an affected show.

If a declared TV path cannot be parsed safely, the helper refreshes that
container rather than risking a stale display. Invalid declarations and
containers without a folder path are skipped without disrupting playback.

### Collision-Free Tokens

Each invalidation receives a token that is unique even when multiple updates
occur in the same second. The current integer Unix timestamp can repeat and
leave a content URL unchanged. A random UUID hex value provides a
process-independent cache buster without pretending that the token is a
persisted timestamp.

### Playback Flow

After `_record_playstate` successfully writes the episode resume point and play
count to the Kodi database, it will:

1. Execute `Container.Refresh` to preserve the existing refresh of the current
   outer listing.
2. Call the shared nested-container helper with the affected Plex item.
3. Continue scheduling the existing file-table cleanup task.

The nested invalidation runs for every successful episode playstate write,
whether the result is watched or partially watched. This keeps progress bars,
resume labels, and watched indicators consistent.

Movies and other video types retain the current outer-container refresh but do
not trigger episode-panel invalidation.

### Removal of the Full-Skin Reload Branch

Remove `reload_skin` from `_playback_progress`, including its initialization,
threshold-mismatch decisions, debug message, and return value. Update
`_record_playstate` to consume the remaining four values and always use the
scoped refresh flow. No playback stop path in `kodimonitor.py` will call
`ReloadSkin()`.

This is removal of a conditional code branch, not deletion of a Git branch.
Git history attributes the full-skin fallback to upstream PKC changes from 2018
and 2023, not to Hermes.

## Error Handling

Nested-container invalidation is best-effort UI work after the database write.
A malformed skin declaration must not roll back or prevent a valid playstate
update. Expected invalid input is skipped. Unexpected helper failures are
logged with enough context to diagnose the affected container and must not
interrupt the remaining stop cleanup.

Plex database lookups should be batched or cached per invalidation call so that
multiple declared containers do not repeatedly query the same episode.

## Verification

Automated tests will prove:

- supported `titles` and `genres` TV paths map to the correct show;
- only relevant declared containers receive new tokens;
- unparseable TV paths fail open while invalid IDs and non-TV paths are skipped;
- successive invalidations generate distinct tokens;
- watched and partial-resume episode writes both refresh the outer container
  and signal the nested container;
- movie writes do not signal episode containers;
- `_playback_progress` retains its playcount and resume decisions after its
  return contract is reduced;
- no `ReloadSkin()` or `reload_skin` playback branch remains.

Run the focused tests first, followed by the applicable PKC test suite,
`compileall`, and `git diff --check`. On the living-room CoreELEC device, verify
that stopping one completed episode and one partial episode updates the nested
Arctic Fuse episode panel without resetting the skin or moving the user out of
the current view.

## Delivery

Preserve the unrelated local modification in `resources/lib/transfer.py` and
stage only files belonging to this change. Because `python3-beta` is a
published branch, the implementation commit will include the required
`addon.xml` version bump and will be pushed only after automated and live
verification succeed. The design document will travel in that implementation
commit rather than creating a spec-only release commit.
