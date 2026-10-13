# -*- coding: utf-8 -*-
"""
For a non-library item WITHOUT a Plex ratingKey (e.g. from TMDb Helper),
send its exact TMDb ID to the main Python instance before adding it to the
Plex Watchlist. TMDb Helper detail dialogs can expose that identity only via
their Home-window monitor properties.
"""

from urllib.parse import urlencode

from xbmcgui import Window

from resources.lib import transfer


def main():
    import xbmc

    window = Window(10000)
    tmdb_id = xbmc.getInfoLabel("ListItem.UniqueID(tmdb)") or xbmc.getInfoLabel(
        "ListItem.Property(tmdb_id)"
    )
    tmdb_type = xbmc.getInfoLabel("ListItem.DBTYPE")
    if not tmdb_id:
        tmdb_id = window.getProperty("TMDbHelper.ListItem.Monitor.TMDb_ID")
        tmdb_type = window.getProperty("TMDbHelper.ListItem.Monitor.TMDb_Type")
    if not tmdb_id:
        return
    args = {"tmdb_id": tmdb_id, "tmdb_type": tmdb_type}
    transfer.plex_command("WATCHLIST_ADD_TMDB?%s" % urlencode(args))


if __name__ == "__main__":
    main()
