# PlexKodiConnect

Primez maintains this PlexKodiConnect (PKC) fork. PKC connects Kodi to a Plex Media Server (PMS) and syncs selected libraries into Kodi's databases. Other skins and add-ons can use the synced library.

PKC changes Kodi's video and music databases. Existing Kodi library data can be lost. Back up the Kodi profile before the first sync.

## Install

1. Install the [Primez repository ZIP](https://primez-x.github.io/kodi.addons/repository.primez.addons/repository.primez.addons-1.0.0.zip) in Kodi.
2. Install PlexKodiConnect from the Primez repository.
3. Open PKC and follow the setup prompts to connect a PMS and select libraries.
4. Let the initial full sync finish before browsing the synced Kodi libraries.

For central repository details, see the [Primez Kodi add-ons guide](https://github.com/primez-x/kodi.addons/blob/main/README.md).

## Requirements

The manifest declares platform `all`, `xbmc.python` 3.0.0, and `script.module.requests` 2.22.0. The dependency list also includes `plugin.video.plexkodiconnect.movies` and `plugin.video.plexkodiconnect.tvshows` 3.0.2, plus `metadata.themoviedb.org.python` 1.3.1. The manifest does not name a minimum Kodi release or a tested platform list.

## Setup and normal use

- Select the Plex libraries that PKC should sync. PKC runs a full sync on first setup, then syncs again on Kodi startup.
- Browse synced media from Kodi's video and music libraries. Use PKC add-on paths when unsure about direct-path setup.
- Open PKC settings to run a manual library sync or adjust the full-sync interval. The default full-sync interval is 60 minutes.
- Plex Companion is enabled by default. Companion lets Plex clients send media and playback commands to Kodi. Restart Kodi after changing this setting.
- Playback mode defaults to **Try Direct Path**. Other choices include Direct Play, Direct Stream, and Force Transcode.
- Plex chooses audio and subtitle streams by default. Set the audio or subtitle stream option to Kodi when Kodi should choose. During transcoding, **Auto-pick audio and subtitle stream using Plex defaults** can select Plex's preferred streams.
- PKC syncs playlists when playlist sync is enabled. Settings can limit sync to playlists with a chosen name prefix.

The upstream [Direct Play guide](https://github.com/croneter/PlexKodiConnect/wiki/Direct-Play) and [FAQ](https://github.com/croneter/PlexKodiConnect/wiki/faq) provide more background. Those pages describe the upstream project; use the Primez installation steps above for this fork.

## Source layout

- `default.py`: Kodi browsing and playback entry point.
- `service.py`: background service entry point.
- `context_*.py`: Kodi context-menu actions.
- `resources/lib/library_sync/`: Plex library and metadata sync.
- `resources/lib/plex_api/`: PMS API handling.
- `resources/lib/plex_db/` and `resources/lib/kodi_db/`: local database access.
- `resources/lib/plex_companion/`: Plex Companion polling and command handling.
- `resources/lib/playback_decision.py` and `resources/lib/subtitles.py`: playback and subtitle selection.
- `resources/settings.xml`: PKC settings.
- `tests/`: configured automated test suite.

## Tests

Run the configured command from the add-on root:

```sh
python3 -m unittest discover -s tests
```

This command matches `.primez-publish.json`.

## Maintenance

- Primez publication tracks the `python3-beta` branch.
- Enable the local publish hook with `git config core.hooksPath .githooks`.
- Changes to shipped behavior require an `addon.xml` version step and a top `<news>` entry. README-only changes need no version bump.
- Follow the [Primez versioning rules](https://github.com/primez-x/kodi.addons/blob/main/VERSIONING.md) and the [central repository guide](https://github.com/primez-x/kodi.addons/blob/main/README.md).
- Report add-on issues in the [Primez PlexKodiConnect repository](https://github.com/primez-x/plugin.video.plexkodiconnect/issues).

## Credits and license

- Based on [PlexKodiConnect](https://github.com/croneter/PlexKodiConnect) by croneter.
- The upstream project credits code from [Emby for Kodi](https://github.com/MediaBrowser/plugin.video.emby), Plex Companion work from [Hippojay](https://github.com/hippojay), and Plex API work from [PlexConnect](https://github.com/iBaa/PlexConnect).
- PKC declares the [GNU GPL version 2 license](LICENSE).
