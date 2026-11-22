# third_party

## rurik/

Copied verbatim from `Other_Repos_Research/Rurik-main` (MIT, see `rurik/LICENSE`):
`toolkit/content.py`, `toolkit/vaultpath.py` and the `toolkit/mapdata/` modules the
map build and the archive writer need; and, for the read-only World Map window,
`toolkit/gwpe.py`, `toolkit/authsrv/gwcrypto.py` and `toolkit/clientscan/`
`areatable.py`, `worldmap.py`, `consttable.py`, `buildid.py`, `pinned.py` and
`textrec.py`, plus `toolkit/mapdata/tilerender.py` for compass art (the area table, the atlas tile tables, and map names from Gw.dat's
text records). They are not edited here, so a fix goes
upstream first and is copied over again.

Rule 10 (every file is ASCII) is waived for these files only: a few carry
non-ASCII characters in comments, and rewriting them would break "verbatim".
Everything under `kryta_cs/` is ASCII.

The editor reaches them through `kryta_cs.paths.bootstrap_rurik()`, which puts
`rurik/toolkit/mapdata`, `rurik/toolkit`, `rurik/toolkit/clientscan` and
`rurik/toolkit/authsrv` on `sys.path` and points
`RURIK_VAULT` at `~/.kryta_cs/rurik-vault`, so nothing is written into this tree.
