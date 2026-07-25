# -*- coding: utf-8 -*-
"""
For a non-library item WITHOUT a Plex ratingKey (e.g. from TMDb Helper),
send a request to our main Python instance to resolve it via title+year
search against Plex Discover, then remove it from the Plex Watchlist.
"""

from urllib.parse import urlencode

from xbmc import sleep
from xbmcgui import Window


def main():
    import xbmc

    title = xbmc.getInfoLabel("ListItem.Title")
    year = xbmc.getInfoLabel("ListItem.Year")
    plex_type = xbmc.getInfoLabel("ListItem.DBTYPE")
    if not title:
        return
    args = {"title": title, "year": year, "plex_type": plex_type}
    window = Window(10000)
    while window.getProperty("plexkodiconnect.command"):
        sleep(20)
    window.setProperty(
        "plexkodiconnect.command", "WATCHLIST_REMOVE_SEARCH?%s" % urlencode(args)
    )


if __name__ == "__main__":
    main()
