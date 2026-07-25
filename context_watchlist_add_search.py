# -*- coding: utf-8 -*-
"""
For a non-library item WITHOUT a Plex ratingKey (e.g. from TMDb Helper),
send its exact TMDb ID to the main Python instance before adding it to the
Plex Watchlist.
"""

from urllib.parse import urlencode

from xbmc import sleep
from xbmcgui import Window


def main():
    import xbmc

    tmdb_id = xbmc.getInfoLabel("ListItem.UniqueID(tmdb)") or xbmc.getInfoLabel(
        "ListItem.Property(tmdb_id)"
    )
    tmdb_type = xbmc.getInfoLabel("ListItem.DBTYPE")
    if not tmdb_id:
        return
    args = {"tmdb_id": tmdb_id, "tmdb_type": tmdb_type}
    window = Window(10000)
    while window.getProperty("plexkodiconnect.command"):
        sleep(20)
    window.setProperty(
        "plexkodiconnect.command", "WATCHLIST_ADD_TMDB?%s" % urlencode(args)
    )


if __name__ == "__main__":
    main()
