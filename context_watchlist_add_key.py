# -*- coding: utf-8 -*-
"""
Grabs the Plex ratingKey for a non-library (discover) item the context menu was
called for and sends a request to our main Python instance to add it to the
Plex Watchlist.
"""

from urllib.parse import urlencode

from resources.lib import transfer



def main():
    import xbmc

    rating_key = xbmc.getInfoLabel("ListItem.Property(ratingKey)")
    plex_type = xbmc.getInfoLabel("ListItem.DBTYPE")
    if not rating_key:
        return
    args = {"rating_key": rating_key, "plex_type": plex_type}
    transfer.plex_command("WATCHLIST_ADD_KEY?%s" % urlencode(args))


if __name__ == "__main__":
    main()
