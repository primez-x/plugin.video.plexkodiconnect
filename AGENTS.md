# Repository Guidelines

## Project Structure & Module Organization
PlexKodiConnect is a Kodi add-on. Root scripts are Kodi entry points: `default.py` for plugin navigation, `service.py` for background service startup, and `context_*.py` for context menu actions. Most add-on logic lives in `resources/lib/`, with focused subpackages such as `app/`, `library_sync/`, `plex_api/`, `plex_db/`, `kodi_db/`, `windows/`, and `playlists/`. Kodi settings, language files, skins, and media assets are under `resources/settings.xml`, `resources/language/`, and `resources/skins/`. Root images such as `icon.png`, `fanart.jpg`, and `themoviedb.png` are add-on assets referenced by `addon.xml`.

## Build, Test, and Development Commands
This repository has no standard package manager or build system. Useful local checks are:

- `python -m compileall default.py service.py context_*.py resources/lib`: compile Python files without importing Kodi-only modules.
- `cd ..; Compress-Archive -Path .\plugin.video.plexkodiconnect -DestinationPath .\plugin.video.plexkodiconnect.zip -Force`: create a Kodi-installable zip from the parent directory on Windows PowerShell.
- Install or symlink this folder as `plugin.video.plexkodiconnect` in Kodi's `addons` directory, then restart Kodi to exercise plugin and service entry points.

## Coding Style & Naming Conventions
Follow PEP 8 with 4-space indentation. Keep modules and functions `snake_case`, constants `UPPER_CASE`, and classes `CamelCase`. Existing files commonly use UTF-8 headers, grouped imports, and module loggers like `LOG = logging.getLogger('PLEX.default')`; match those patterns. Keep Kodi-facing entry scripts thin and move reusable behavior into `resources/lib/`. Treat vendored third-party packages under `resources/lib/` carefully and avoid broad formatting churn.

## Testing Guidelines
Run the repository's automated suite with `python -m unittest discover -s tests -v`. At minimum, run `python -m compileall` before submitting Python changes. For behavior changes, perform a Kodi smoke test with the supported Kodi versions noted in `README.md`, and document Plex server conditions, affected media type, and whether a Kodi database reset was required. If adding tests, place them under `tests/` and mirror the module path, for example `tests/library_sync/test_full_sync.py`.

For automatic Up Next handoffs validated by item identity, handoff key, and playback generation, treat the outgoing item's first positive watched callback as terminal, retain its terminal fence until an explicit playcount <= 0 unwatch, and queue one PMS watched scrobble. Treat an explicit playcount <= 0 callback for the matching handoff as an authoritative user unwatch: cancel pending restores, clear the matching handoff/fence, and propagate unwatched to Kodi and PMS. Before completion, only reset-shaped callbacks without an explicit playcount may refresh Kodi from current PMS metadata; after completion, synchronously repair Kodi to watched/no-resume and retain that fence across timeout and unrelated playback so later reset callbacks cannot undo it. Serialize per-item PMS playstate mutations so the latest authoritative callback is sent last. Callbacks outside the matching handoff retain normal handling; verify settled PMS metadata and Kodi watched/no-resume state.

## Commit & Pull Request Guidelines
Recent commits use short imperative subjects, for example `Fix HTTPS plex.direct playback URLs` and `Remove PAT-based Kodi repository notifier`; keep subjects specific and reference issue numbers when useful. Per `CONTRIBUTING.md`, open pull requests against `develop`, not `master`. Include a concise description, linked issues, Kodi and Plex versions tested, manual test notes, and screenshots for visible skin or dialog changes.

## Security & Configuration Tips
Never post Kodi logs containing Plex tokens. Scrub tokens before attaching logs to issues or pull requests, and do not commit local Kodi profiles, generated databases, credentials, or personal server addresses.

## Primez Publish Rules

The tracked branch is `python3-beta`: every push to it is published to the Primez Kodi repository, and every push is exactly one release. Kodi auto-update follows the repository version, not the Git SHA.

Each push raises the root `addon.xml` version by exactly one step from the branch tip — raise one component by 1 and reset the ones after it — and adds a `<news>` entry on top whose first line names the new version (match the existing format). Which component to raise is decided by `VERSIONING.md` in `primez-x/kodi.addons`: **major** when users or other add-ons must act or something breaks, **minor** for new or changed user-visible behaviour, **patch** for fixes, performance, tooling, tests and docs (take the highest level that applies). A push may hold several commits; only its tip version counts. Adopting an upstream version is the only allowed jump and needs a `Version-Jump: <reason>` line in the tip commit message.

`.githooks/pre-push` enforces this before the push leaves the machine (version above the branch tip, news entry, and the `tests` in `.primez-publish.json` passing on the pushed commit); enable it with `git config core.hooksPath .githooks` (Claude Code sessions do this automatically). `.github/workflows/publish-check.yml` runs the same check on GitHub, and the `kodi.addons` publish refuses commits that fail it. `.githooks/publish_check.py` is a copy of `primez-x/kodi.addons` `tools/publish_check.py`; change it there and re-copy it. Keep `.primez-publish.json` tests in sync with how this repository's tests are run.
