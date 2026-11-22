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
      object_window.py       the Object Window: spawns, maps, terrain, textures, models, props
      model_preview.py        the model properties preview, textured
      model_properties.py     the Models record dialog (measurements and preview)
      prop_properties.py      a placed prop's reference data (placement, xyz, yaw, scale)
      uvmap.py                a model triangle's texture->screen affine map
      terrain_texture.py      the client's base+overlay terrain blend
      panels.py              Landscape Edit Settings, Map Properties, Problems, Messages
      main_window.py         the window, menus, toolbar and dock layout
    third_party/rurik/       Rurik's toolkit, verbatim (see third_party/NOTICE.md)
    tests/                   offline tests: python -m pytest tests

## Next

- Dragging a prop's yaw and scale directly in the Render Window; the Reference
  Data dialog edits both today.
- NPC spawns as a second object type beside the spawn points.
- Doorways (`map_arch`) between custom maps.
