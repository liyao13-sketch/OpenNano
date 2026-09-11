#!/usr/bin/env python3
"""klink 实时版:把光栅版图绘制进运行中的 KLayout GUI(读 KLINK_CONFIG env)。

前提:KLayout GUI 已开、klink 插件监听 127.0.0.1:8765。
config = {pitch_nm, linewidth_nm, nx, ny, cell_um, label}
"""
import json
import os
import sys

from klink import KLinkClient

cfg_path = os.environ.get("KLINK_CONFIG") or (sys.argv[1] if len(sys.argv) > 1 else None)
cfg = json.load(open(cfg_path, encoding="utf-8"))
pitch_nm = max(float(cfg.get("pitch_nm", 1000)), 1)
lw_nm = max(float(cfg.get("linewidth_nm", 400)), 1)
nx = max(int(cfg.get("nx", 1)), 1)
ny = max(int(cfg.get("ny", 1)), 1)
cell_um = max(float(cfg.get("cell_um", 100)), 1)
label = cfg.get("label", "OpenNano")

with KLinkClient() as c:
    try:
        c.cell_delete("TOP", recursive=True)
    except Exception:
        pass
    c.cell_create("TOP")
    c.layer_ensure(1, 0, name="GRATING")
    c.layer_ensure(99, 0, name="LABEL")

    n = max(int(cell_um * 1000 / pitch_nm), 1)
    boxes, labels = [], []
    for iy in range(ny):
        for ix in range(nx):
            cx = (ix - (nx - 1) / 2.0) * cell_um
            cy = (iy - (ny - 1) / 2.0) * cell_um
            start_y = cy - (n * pitch_nm / 1000.0) / 2.0
            x0, x1 = cx - cell_um / 2.0, cx + cell_um / 2.0
            for i in range(n):
                y0 = start_y + i * pitch_nm / 1000.0
                boxes.append([x0, y0, x1, y0 + lw_nm / 1000.0])
            labels.append((cx, cy - cell_um / 2.0 - 5.0, f"{label}-r{iy * nx + ix + 1}"))

    c.shape_insert_boxes(cell="TOP", layer=1, boxes_um=boxes)
    for tx, ty, s in labels:
        c.shape_insert_text(cell="TOP", layer=99, position_um=[tx, ty], string=s, size_um=4)
    c.show_cell("TOP", zoom_fit=True)
    print(f"drawn {len(boxes)} grating lines into KLayout TOP")
