# -*- coding: utf-8 -*-
"""Send the focused title's exact external ID to the Plex Watchlist service."""

from urllib.parse import urlencode

from resources.lib import transfer


def main():
    import xbmc

    tmdb_id = xbmc.getInfoLabel("ListItem.UniqueID(tmdb)") or xbmc.getInfoLabel(
        "ListItem.Property(tmdb_id)"
    )
    tmdb_type = xbmc.getInfoLabel("ListItem.DBTYPE")
    if not tmdb_id:
        return
    args = {"tmdb_id": tmdb_id, "tmdb_type": tmdb_type}
    transfer.plex_command("WATCHLIST_REMOVE_TMDB?%s" % urlencode(args))


if __name__ == "__main__":
    main()
