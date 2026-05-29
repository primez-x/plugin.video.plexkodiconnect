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
No automated test suite is currently included. At minimum, run `python -m compileall` before submitting Python changes. For behavior changes, perform a Kodi smoke test with the supported Kodi versions noted in `README.md`, and document Plex server conditions, affected media type, and whether a Kodi database reset was required. If adding tests, place them under `tests/` and mirror the module path, for example `tests/library_sync/test_full_sync.py`.

## Commit & Pull Request Guidelines
Recent commits use short imperative subjects, for example `Fix HTTPS plex.direct playback URLs` and `Remove PAT-based Kodi repository notifier`; keep subjects specific and reference issue numbers when useful. Per `CONTRIBUTING.md`, open pull requests against `develop`, not `master`. Include a concise description, linked issues, Kodi and Plex versions tested, manual test notes, and screenshots for visible skin or dialog changes.

For Primez repository publishing, any commit pushed to the tracked `python3-beta` branch must bump the root `addon.xml` version in the same commit. Kodi auto-update consumes the generated repository version, not the Git SHA, and the central `kodi.addons` publish guard rejects webhook publishes whose source version does not increase.

## Security & Configuration Tips
Never post Kodi logs containing Plex tokens. Scrub tokens before attaching logs to issues or pull requests, and do not commit local Kodi profiles, generated databases, credentials, or personal server addresses.
