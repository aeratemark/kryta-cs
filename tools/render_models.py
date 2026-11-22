"""Render every donor prop model to a PNG, so the Models list can be named by eye.

    py -3.12 tools/render_models.py [--dat PATH] [--donor 0xFILEID | --map map.kmap.json]
                                    [--out DIR] [--size 320] [--per-sheet 20]

For each model in the donor's prop-model list (the editor's Models tab) this
writes `<out>/model_<index>_<fileid>.png`: the model from two opposite sides,
drawn by the editor's own ModelPreview (same textures, same shading). It also
writes `<out>/sheet_NN.png` contact sheets and `<out>/models.csv`.

Defaults: the archive from the editor's preferences (~/.kryta_cs/settings.json),
the editor's default donor, and tools/model_renders. Reads only.
"""
import argparse
import csv
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtCore import QRectF, Qt                              # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter          # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402

from kryta_cs import assets, paths                                 # noqa: E402
from kryta_cs.model import DEFAULT_DONOR_FILE_ID                   # noqa: E402
from kryta_cs.ui import model_preview, theme                       # noqa: E402
from kryta_cs.ui.model_preview import ModelPreview                 # noqa: E402

ANGLES = (32, 212)            # degrees of yaw: one side, then the other
LABEL_H = 26


def _int(s):
    return int(str(s), 0)


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dat", default=None, help="Gw.dat (default: the editor's chosen archive)")
    ap.add_argument("--donor", type=_int, default=None, help="donor map file id, e.g. 0x1B97D")
    ap.add_argument("--map", default=None, help="take the donor from this .kmap.json")
    ap.add_argument("--out", default=os.path.join(HERE, "model_renders"))
    ap.add_argument("--size", type=int, default=320, help="pixels per view")
    ap.add_argument("--per-sheet", type=int, default=20)
    return ap.parse_args(argv)


def render_views(app, preview, ma, size):
    """The model from each angle in ANGLES, side by side, as one QImage."""
    img = QImage(size * len(ANGLES), size, QImage.Format_RGB32)
    img.fill(QColor(40, 40, 40))
    qp = QPainter(img)
    for i, deg in enumerate(ANGLES):
        model_preview.YAW = math.radians(deg)
        preview.set_assets(ma)
        for _ in range(3):
            app.processEvents()
        qp.drawPixmap(i * size, 0, preview.grab())
    qp.end()
    return img


def labelled(img, text):
    out = QImage(img.width(), img.height() + LABEL_H, QImage.Format_RGB32)
    out.fill(QColor(20, 20, 20))
    qp = QPainter(out)
    qp.drawImage(0, LABEL_H, img)
    qp.setPen(QColor(235, 235, 235))
    f = QFont()
    f.setPointSize(11)
    f.setBold(True)
    qp.setFont(f)
    qp.drawText(QRectF(8, 0, out.width() - 16, LABEL_H), Qt.AlignLeft | Qt.AlignVCenter, text)
    qp.end()
    return out


def write_sheets(tiles, out_dir, per_sheet):
    if not tiles:
        return []
    cols = 4
    tw, th = tiles[0].width() // 2, tiles[0].height() // 2   # half size on the sheet
    names = []
    for s in range(0, len(tiles), per_sheet):
        chunk = tiles[s:s + per_sheet]
        rows = math.ceil(len(chunk) / cols)
        sheet = QImage(cols * tw, rows * th, QImage.Format_RGB32)
        sheet.fill(QColor(10, 10, 10))
        qp = QPainter(sheet)
        qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for i, t in enumerate(chunk):
            qp.drawImage(QRectF((i % cols) * tw, (i // cols) * th, tw, th), t)
        qp.end()
        name = os.path.join(out_dir, f"sheet_{s // per_sheet + 1:02d}.png")
        sheet.save(name)
        names.append(name)
    return names


def main(argv):
    args = parse(argv)
    dat = args.dat or paths.load_prefs().get("archive", "")
    donor = args.donor
    if donor is None and args.map:
        with open(args.map, "r", encoding="utf-8") as fh:
            donor = _int(json.load(fh).get("donor_file_id", DEFAULT_DONOR_FILE_ID))
    if donor is None:
        donor = DEFAULT_DONOR_FILE_ID
    os.makedirs(args.out, exist_ok=True)
    print(f"archive: {dat}\ndonor:   0x{donor:X}\nout:     {args.out}")

    app = QApplication([])
    theme.apply(app, "dark")
    preview = ModelPreview()
    preview.setAttribute(Qt.WA_DontShowOnScreen, True)
    preview.resize(args.size, args.size)
    preview.show()

    t0 = time.time()
    donor_assets = assets.load_donor_assets(dat, donor, log=print)
    models = donor_assets.models
    print(f"{len(models)} model(s) in the donor's list")

    rows, tiles, seen = [], [], set()
    for m in models:
        tag = f"#{m.index}  0x{m.file_id:X}"
        row = {"index": m.index, "file_id": f"0x{m.file_id:X}", "tris": m.n_tris,
               "submodels": m.n_submodels,
               "cells": "" if m.cells is None else f"{m.cells:.2f}",
               "height": "" if m.height is None else f"{m.height:.1f}",
               "placed": m.placed, "png": "", "error": m.error or ""}
        if m.file_id in seen:
            row["error"] = "same file id as an earlier row"
            rows.append(row)
            continue
        seen.add(m.file_id)
        if m.extent is None:
            print(f"{tag}: skipped ({m.error or 'not measured'})")
            rows.append(row)
            continue
        try:
            ma = assets.load_model_assets(dat, m.file_id)
            img = render_views(app, preview, ma, args.size)
        except Exception as exc:                                     # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
            print(f"{tag}: {row['error']}")
            rows.append(row)
            continue
        size_txt = "" if m.cells is None else f"  {m.cells:.1f} cells, h {m.height:.0f}"
        tile = labelled(img, f"{tag}  {m.n_tris} tris{size_txt}")
        name = f"model_{m.index:03d}_{m.file_id:X}.png"
        tile.save(os.path.join(args.out, name))
        tiles.append(tile)
        row["png"] = name
        rows.append(row)
        print(f"{tag}: {name}")

    with open(os.path.join(args.out, "models.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["index"])
        w.writeheader()
        w.writerows(rows)
    sheets = write_sheets(tiles, args.out, args.per_sheet)
    print(f"done: {len(tiles)} model PNG(s), {len(sheets)} sheet(s) in {time.time() - t0:.0f}s")
    preview.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
