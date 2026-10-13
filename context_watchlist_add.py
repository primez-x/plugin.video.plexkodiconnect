# -*- coding: utf-8 -*-
"""
Grabs kodi_id and kodi_type for the Kodi item the context menu was called for
and sends a request to our main Python instance
"""
from urllib.parse import urlencode

from resources.lib import transfer
from resources.lib.contextmenu.common import kodi_item_from_listitem


def main():
    kodi_id, kodi_type = kodi_item_from_listitem()
    args = {
        'kodi_id': kodi_id,
        'kodi_type': kodi_type
    }
    transfer.plex_command('WATCHLIST_ADD?%s' % urlencode(args))


if __name__ == "__main__":
    main()