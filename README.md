# Kryta Construction Set

A map editor for Kryta in the spirit of the TES Construction Set: sculpt a map,
check that the client can walk it, then build it, install it into `Gw.dat` and
publish it to the world from one window.

The map file itself is written by Rurik's toolkit (`third_party/rurik`, MIT),
the only writer known to produce a map the client loads and walks. This tool
is the editor and the glue around it: a map project format, terrain tools,
the checks Rurik's build would refuse on, the archive install, and the
world-pack folder that makes the world serve the map.

This folder replaced the old `asura_cs` editor, which never fully worked.

## Run it

    pip install -r requirements.txt
    python run_editor.py [map.kmap.json]

It needs a 64-bit Python 3.10-3.12: PySide6 has no wheels for 32-bit Python, and
the default `python` on this PC is a 32-bit 3.13. Use e.g.
`py -3.12 -m pip install -r requirements.txt` and `py -3.12 run_editor.py`.

If the constants donor (`0x22E2C` by default) is not in the archive, the build
picks a substitute by Rurik's criterion: a map whose Zones chunk is the minimal
34-byte form (Header and Zones are the only chunks the donor lends).

Map projects are saved as `data/maps/custom/<slug>.kmap.json` in the Kryta
checkout (ASCII JSON, one file per map).

## The window

The default look is the classic grey Construction Set style; View > Theme switches to the dark theme.

The screen is laid out the way the Morrowind Construction Set laid it out:

- **Render Window** (centre): the 3D editor, by default, through a perspective
  camera as the CS's was. Left drag sculpts with the current brush; right drag
  paints the donor texture selected in the Object Window's Textures tab. The red
  circle is the edit radius; Ctrl+wheel sizes it. The camera moves as the
  Morrowind CS's did: hold **Shift and move the mouse** to rotate the view around
  the selected object (or turn in place with nothing selected), middle drag or
  **Space + mouse move** pans, the wheel (with or without Shift) zooms toward
  the point under the mouse, the arrow keys move the view and Shift+Up/Down
  raises and lowers it, F fits. Shift or Alt + middle drag orbits the point under
  the mouse. Heights are always drawn true to scale.
  W toggles wireframe, B the cell borders, H (or S) the landscape brush. **Top View** (Ctrl+2) is the same map from above for precise
  work; 3D Render is Ctrl+1.
- **Cell View** (under it): the map's 32-cell tiles on the left (Cell Name, Grid,
  Ref Co., Path) and the spawn points in the selected tile on the right (Object
  ID, Type, Ownership). Click a tile to move the Render Window there; double-click
  an object to select it and centre on it.
- **Object Window** (left): the CS record browser - Spawn Points, Maps, Terrain,
  Textures, Models and Props. The Textures and Models tabs are the donor's own
  records, read out of your `Gw.dat`; the Props tab is what you have placed.
  Double-click a map to open it; the open map's row is bold. Drag a model row
  from Models into the Render Window to place it; right-click a model for its
  properties and a textured 3D preview. A placed model is an object, edited in
  **Select mode** only (in Sculpt mode clicks on it edit the ground): left click
  selects it (a yellow box marks it), drag moves it along the ground, right drag
  turns it, Delete removes it, and double-click opens its reference data.
  A placed model is **grid locked** by default - it stands on the nearest cell
  centre at that cell's height, which is how Guild Wars places props - and its
  reference data offers **Free place** for a position anywhere on the ground.
  Its X/Y/Z, yaw and scale are editable there too.
- **Landscape Edit Settings** (View > Landscape Edit Settings, H): the brush,
  raise/lower/smooth/flatten/level/roughen, and whole-map generators,
  terrace-to-48 and smooth-all. Hidden by default, as the CS hid it.
- **Map Properties**, **Problems**, **Messages**, **Undo History**: View menu
  windows. Problems (F7) is everything the build would refuse on, or that the
  client would leave unwalkable.
- **Spawn points are objects**, like props, and there is no separate spawn tool:
  in Select mode click one to select it, drag to move, Alt+drag to turn, Delete
  to remove; Character > Place Spawn Point (or Add in the Spawn Points tab) puts
  a new one where the Render Window is looking. The Cell View's object list and
  the Object Window's Spawn Points tab treat them as records; placed models are objects
  beside them in the Cell View (Object ID, Type, Ownership). Spawn 0 is also
  the flood seed the client's mesh compiler starts from, so it must stand on
  flat ground.
- **Textures and models**: the Object Window reads the donor map's terrain
  texture table and prop-model list out of `Gw.dat` (status bar: "Assets: ...").
  Selecting a texture sets the right-drag brush; the per-cell texture layer is
  written into the built map, and the Render Window shows it through the
  client's own layer blend: four corner tiles per cell, grouped by tile type,
  up to three alpha-masked layers, the `trnvariation` quadrant and the shader's
  1.4 brightness (bound to Rurik's `trnblend`/`trnvariation`, the measured port
  of GuildWarsMapBrowser's `Terrain.cpp`/`TerrainRevPixelShader.hlsl`).
  Models are drawn with their own textures, through the chain Rurik recovered:
  a submodel's material layer names its texture slot in the model's `0xFA5`
  list, and the submodel's UV set maps that texture onto its triangles
  (`assets.load_model_assets`, `ui/uvmap.py`). A model's axes are the map's -
  x/y are the ground plane and **z is height, and it points down**, so a world
  point is the prop plus `(x, y, -z)`; no other axis swap is needed, only the
  model's three rotation bytes. The Render Window draws with OpenGL
  (`ui/view3d_gl.py`): the terrain is the real height field draped with the
  blended albedo, models are their full textured meshes, and a depth buffer
  decides what is in front. If OpenGL cannot start it falls back to the old
  QPainter view; `KRYTA_CS_SOFTWARE_3D=1` forces that fallback.
  **Water** is a plane at the environment's level: the Render Window fills every
  cell whose ground sits below it, and **Map Properties > Water** sets the
  height the build writes into the environment's water record. It used to keep
  the donor's plane (0 on pre-Searing's map), which is why water appeared in
  patches that had nothing to do with this map's ground.
  **Universe > World Map** (Ctrl+M), read-only: every row of the client's area
  table (`s_missionClientData`: id, name, world, region, type, both footprint
  rects) beside the chosen world's atlas stitched from `Gw.dat`, footprints
  outlined (outpost rect solid, game rect dashed). Click a rect or a row to
  select it; the "free-looking" column is a heuristic. Reads `Gw.exe` next to
  the configured `Gw.dat`; stitched worlds are cached under
  `~/.kryta_cs/worldmap-cache/`.

## Flora (the Flora tool, G)

Guild Wars grows its ground cover - wheat, grass, flowers, ferns, shrubs - itself, inside flora
areas a map file carries (its Zones part; `kryta_cs/zones.py` reads and writes it). Each area
names a flora TYPE the map also carries: groups of small plant models with sizes, densities and
pick weights, named after ArenaNet's setup file (`LakesideWheat`, `EdenHillsShrubs`). They are not
props and not part of a texture; that is why pre-Searing's grass is patchy.

The flora brush paints those areas. Object Window > Flora lists the donor's flora types
(pre-Searing, 0x1B97D, has eight); pick one and the Flora tool arms. In the Render Window, left drag
paints where it grows, right drag clears; the edit radius is the brush's (Ctrl+wheel), and every
stroke is one undo. Painted flora shows as a see-through colour per type; the Flora tab's checkbox
hides it.

The build turns the painted cells into rectangles and writes the map's own Zones part: the
donor's flora types and plant models (the Zones Dependencies list) exactly as the donor has them,
and your areas. A type you did not paint has no area, so nothing of it grows. The types are kept
whole because the Zones part's record 2 points into the plant list; a build that dropped unused
types crashed the client on load (`Assertion: def`, ZnModel.cpp). The client grows the plants when
it compiles the map, so what you see in game is the game's own scatter, not a copy of the editor's
colour. A map with no painted flora builds as before.

## Game maps (File > Open Game Map)

Opens one of the world pack's maps as it ships in Gw.dat: its ground and textures to look at, its
props, flora areas and NPCs to edit. Pick it from the list (map id, name, kind, map file); maps
that share a map file are named there, because editing the file's props or flora changes all of
them (pre-Searing's file is both Lakeside County and Ascalon City). NPCs are per map id.

- Props: the map's own, selected, moved, turned and deleted as any prop; new ones come from the
  map's own model list in the Models tab.
- Flora: the map's own areas are drawn as outlines with the Flora tool's overlay; right-click
  inside one to remove it, and paint new ones with the map's own flora types.
- NPCs: the pack's `npcs/placements/<map id>.json`, every row's other fields, its routes and its
  note kept as they are. A row that faces by a `direction` keeps it until you turn the NPC.
- The ground is not reshaped: the Sculpt tool is off for a game map, and its id, type, water,
  mesh flags and donor are fixed.

Build & Deploy patches the map's own file: the props part and its model list, and the flora part,
everything else carried byte for byte (kryta_cs/stock.py). Every build starts from the map as it
shipped: the copy the editor keeps in `kryta-maps/` from just before it first writes the map, else
the `Gw.dat.kryta-original` backup, else Gw.dat. So a build that changed nothing writes the map's
own bytes back, and Restore Gw.dat undoes it. Gw.dat has a map's file only once the official client
downloaded it; a map it lacks cannot be opened. Publish writes the map's NPC file in place (git
undoes that). `tools/game_map_check.bat` opens and rebuilds real maps from your Gw.dat without
writing anything, to confirm each comes back byte for byte.

## NPCs (Object Window > NPCs, Universe > NPCs)

The NPCs tab lists the world pack's NPC types (`npcs/definitions.json`) over the NPCs placed on
this map. Drag a type into the Render Window to place one; in Select mode click an NPC to select
it, drag to move it, Alt-drag to turn it, Delete to remove it, and double-click (here or in the
list) for its record: type, name, position, facing, level and behavior (an entry of
`npcs/behaviors.lua`). Every change is one undo.

An NPC is drawn as its own model, read from Gw.dat with the prop models and standing in its rest
pose, turned to its facing and sized by its type's scale; a ring at its feet is green when it is
friendly and red when hostile. A character's model file is only its skeleton: its meshes are the
type's body files (`model_files`), and a type without body files carries its mesh in the model
file itself. A type whose mesh cannot be read (5 of the Vanilla pack's 120) is drawn as a post.
Armour dyes and held weapons are not drawn.

The pack stores a type's name as encoded game text, which the editor cannot read, so a type is
shown by the readable name some placement in the pack gives it (`Duke Barradin`), by its id
(`Bandit Raider`), or as its profession and level. The Name field of a placed NPC is the name the
client shows over it, and the one a dialog in `dialogs/*.lua` finds it by.

The NPCs are saved in the map project. Publish writes them as the world pack's
`npcs/placements/<map id>.json`; when the pack has its own file for that id, Publish moves it to
`kryta-cs-displaced/npcs/placements/` and taking the map off the world moves it back. Publish
refuses a map whose NPCs the world would refuse (a type or behavior not in the pack). Restart the
world to see them. Patrol routes are not edited here yet.

## Scripts (Gameplay > Edit Scripts, Ctrl+E)

Kryta's content is Lua, and the Construction Set edits it where it lives: the pack's own `.lua`
files. Nothing is copied into a project or kept anywhere else; Save writes the file.

The Script Edit window lists every Lua file the world reads, by what the world does with it:

- **Runtime**: `scripts/runtime.lua`, which runs every dialog (`docs/decisions/0019`).
- **Dialogs**: `dialogs/<npc>.lua`, one per NPC (`dialogs/README.md`). **New Dialog** (Ctrl+N) makes
  one in the README's shape.
- **Behaviors**: `npcs/behaviors.lua`, the NPC behaviour trees and scripts.
- **Skills**: every `skills/*.lua` a `skills.json` row names (`skills/README.md`); a script no row
  names is listed with a warning, because the world never reads it.

The text is highlighted (the pack's own names `gw`, `mix`, `PLAYER`, `ctx`, `cast` and `op` picked
out), with line numbers; Tab and Shift+Tab indent four spaces, Ctrl+/ comments lines, Ctrl+F finds,
Ctrl+G goes to a line. Several files can be changed at once; each keeps its own undo history, and the
list marks unsaved files with `*`.

**Check** (F7, this file) and **Check All** (Shift+F7) find what the world would refuse at start-up:

1. Syntax, always, as you type: the line and column of the first error, in Lua's own words.
2. With `lupa` installed (it is in `requirements.txt`), the world's own load: each kind run in a Lua
   5.4 state opened as `game/src/Scripting/` opens it (only `table`, `string`, `math` and the base
   functions; no `load`, `loadfile` or `dofile`; a million instructions per call), then the world's
   checks: the runtime's `check()` and every text converted as `NpcScripts::text_units` converts it
   for the dialogs, `parse_spec` for the behaviours, an `on_use` function for every skill.

A problem names its file and line: double-click it. Unsaved text is checked in place of the file, so a
dialog can be checked against a runtime change not yet saved. Save asks before writing a file with a
syntax error, or one that changed on disk since it was opened. The world reads the pack once, at
start-up: restart it after saving.

From a terminal: `python -m kryta_cs.lua.check [pack]` runs Check All and exits 1 on an error.

The reader behind this (`kryta_cs/lua/source.py`) parses all of Lua 5.4 in plain Python and keeps
every position, so a later window can show a dialog's pages as a form and change one value by
replacing exactly its characters: comments, order, layout and hand-written functions stay as written.

## Build & Deploy (F5)

1. **Check** the map (the Problems list, errors stop here).
2. **Build**: Rurik's `assemble()` makes the map file, borrowing textures and
   constants from the donor maps in `Gw.dat`, then writes the per-cell texture
   layer painted in the Textures tab and the props placed from the Models tab.
   Reads only.
3. **Back up** `Gw.dat` once, as `Gw.dat.kryta-original` beside it.
4. **Install** into `Gw.dat` under a new file id (or the one this map already
   has), with an empty walkable mesh the client fills on first load.
   `Gw.dat` must not be open in the client.
5. **Publish** into the world pack: `config/World/<pack>/maps/Custom/<map id>/`
   with `basic.json` (id, kind, Gw.dat file id, name, and the instance value of
   the map it stands in for), `spawns.json` and `kryta-cs.json` (the editor's
   note). The pack is `WorldPack` in `worldserver.json`, `config/World/Vanilla`
   by default; the world reads it as it starts. A map is a file now, not a
   database row (Kryta's `docs/decisions/0014-the-world-content-is-a-pack.md`).
   The pack's own folder for that id is moved aside to
   `config/World/<pack>/kryta-cs-displaced/`, because the world reads every
   folder and one id must be stated once. Old `custom-map-*.sql` files from
   earlier versions of the editor are removed.
6. **Point the control panel** at the same archive (`tools/manage/manage.json`
   MapArchiveFile). The world no longer reads `Gw.dat` at all: it paths from
   `data/path-graphs.bin`, and the control panel is what copies a map's graph
   into that file. If an older editor cleared `worldserver.json` PathGraphs,
   this step puts it back (the world refuses to start without it).

**Publish only** runs steps 5 and 6 without building or installing again.

`/tp` only knows the fixed travel slots in `game/src/Maps/TravelTargets.rs`,
so a map is published onto one of them (Kamadan, 449, by default) and
`/tp kamadan` loads it. **Take off the world** removes the map's folder from
the pack and moves the pack's own folder for that id back where it was.

Then:

1. Start the stack (`python run_server.py`), log in, `/tp kamadan`.
2. The first load makes the client compile the walkable mesh into `Gw.dat`.
3. Close the client. With the control panel running, click **Update walk
   graph**: the panel reads that mesh out of `Gw.dat` (its MapArchiveFile;
   restart the panel if step 6 just changed it) and writes the map's graph into
   `data/path-graphs.bin`. Until then the map answers NoGraph: you can stand in
   it, but walking is not pathed.
4. Restart the world so it reads the updated cache, go back in and walk.
   **Verify compiled map** reads back what the client built.

After updating Kryta from GitHub, a pack folder the editor had moved aside
can come back; publishing again moves it aside again.

**Restore original Gw.dat** puts the backup back.

## Layout

    run_editor.py            entry point
    kryta_cs/model.py        the map project (format kryta-cs-map/1), heights, textures, spawns
    kryta_cs/terrain.py      slopes, reachability, validation, generators, brushes
    kryta_cs/pipeline.py     build / backup / install / verify through Rurik's toolkit
    kryta_cs/assets.py       the donor's terrain textures and prop models, read from Gw.dat
    kryta_cs/kryta.py        the Kryta checkout: map pack catalogue, /tp slots, pack publish, manager settings
    kryta_cs/zones.py        a map's Zones part (flora types and areas): read, write, round-trip
    kryta_cs/flora.py        the flora brush's layer: painting cells, the rectangles a build writes
    kryta_cs/stock.py        game maps: open one from Gw.dat, build it back as a patch of its own file
    kryta_cs/npcs.py         the pack's NPC types and a map's placed NPCs as npcs/placements/<id>.json
    kryta_cs/lua/            the pack's Lua, without Qt:
      source.py              Lua 5.4 read with positions kept: syntax errors, tables as data, exact edits
      pack.py                which Lua files the world reads, by kind; reading and writing them
      check.py               Check: syntax, then the world's own load through lupa
    kryta_cs/ui/             the PySide6 window
      theme.py               the classic and dark themes
      icons.py               the 16x16 toolbar icons, drawn in code
      chrome.py              CS caption bars, sunken view wells, tool strips
      view3d.py              the 3D Render Window: camera, picking, editing, software fallback
      view3d_gl.py           the OpenGL renderer for it (depth-buffered terrain, models, water)
      glmath.py              its view/projection and prop matrices (Qt-free, tested)
      heightmap_view.py      the Top View
      render_window.py       the Render Window frame and the 3D/Top switch
      cell_view.py           the Cell View: tiles and their objects
      object_window.py       the Object Window: spawns, maps, terrain, textures, flora, models, props, NPCs
      npc_dialog.py           a placed NPC's reference data (type, name, xy, facing, level, behavior)
      model_preview.py        the model properties preview, textured
      model_properties.py     the Models record dialog (measurements and preview)
      prop_properties.py      a placed prop's reference data (placement, xyz, yaw, scale)
      uvmap.py                a model triangle's texture->screen affine map
      terrain_texture.py      the client's base+overlay terrain blend
      panels.py              Landscape Edit Settings, Map Properties, Problems, Messages
      main_window.py         the window, menus, toolbar and dock layout
      script_window.py       Gameplay > Edit Scripts: the pack's Lua files, Check, Save
      lua_editor.py          the script text box: highlighting, line numbers, problem lines
    third_party/rurik/       Rurik's toolkit, verbatim (see third_party/NOTICE.md)
    tests/                   offline tests: python -m pytest tests

## Next

- Dragging a prop's yaw and scale directly in the Render Window; the Reference
  Data dialog edits both today.
- A Dialogue window over `dialogs/*.lua`: pages and choices as a form, written back through
  `lua/source.py`'s exact edits, with a panel that runs a talk through the runtime for a test
  character.
- Patrol routes for placed NPCs: waypoints drawn and dragged in the Render Window, written as the
  placements file's `routes`.
- Doorways (`map_arch`) between custom maps.

## License

The Kryta Construction Set is released under the MIT License; see `LICENSE`. The Rurik toolkit
copied under `third_party/rurik/` keeps its own MIT license (`third_party/rurik/LICENSE`, see
`third_party/NOTICE.md`). Guild Wars and its game files belong to ArenaNet: none are part of this
repository, and the tools that read your own Gw.dat write their output to git-ignored folders.
