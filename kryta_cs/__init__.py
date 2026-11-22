"""Kryta Construction Set - the world editor for the Kryta server.

A Morrowind-Construction-Set style editor, written in Python with PySide6. Its first
module authors a brand-new map: sculpt a height field, place the spawn points, then
build it into the client's own archive (Gw.dat) under a file id ArenaNet never used,
and add the map row the server hosts it from.

The archive half is Rurik's map toolkit (MIT, vendored under third_party/rurik), which
was proven against a retail client; this package is the editor around it and the
Kryta-side half (the map row, the server's settings).
"""

__version__ = "0.1.0"
APP_NAME = "Kryta Construction Set"
