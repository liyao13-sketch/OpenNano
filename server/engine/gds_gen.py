#!/usr/bin/env python3
"""通用 GDS 生成器(klayout 无头运行,读 JSON 配置)。

用法: klayout -b -r gds_gen.py <config.json>
config = {output, pitch_nm, linewidth_nm, nx, ny, cell_um, label}
"""
import json
import sys

import pya

import os
cfg_path = os.environ.get("GDS_CONFIG") or (sys.argv[1] if len(sys.argv) > 1 else None)
cfg = json.load(open(cfg_path, encoding="utf-8"))
out = cfg["output"]
pitch = max(int(cfg.get("pitch_nm", 1000)), 1)
line = max(int(cfg.get("linewidth_nm", 500)), 1)
nx = max(int(cfg.get("nx", 1)), 1)
ny = max(int(cfg.get("ny", 1)), 1)
cell_um = max(int(cfg.get("cell_um", 100)), 1)
label = cfg.get("label", "OpenNano")
cell_nm = cell_um * 1000

ly = pya.Layout()
ly.dbu = 0.001          # 1 unit = 1 nm
top = ly.create_cell("TOP")
li = ly.layer(1, 0)     # 图形层
lt = ly.layer(99, 1)    # 标识层

grating = ly.create_cell("GRATING")
x = 0
while x + line <= cell_nm:
    grating.shapes(li).insert(pya.Box(x, 0, x + line, cell_nm))
    x += pitch

for iy in range(ny):
    for ix in range(nx):
        t = pya.Trans(cell_nm * ix, cell_nm * iy)
        top.insert(pya.CellInstArray(grating.cell_index(), t))
        txt = pya.Text(f"{label}-r{iy * nx + ix + 1}", pya.Trans(cell_nm * ix + 2000,
                                                                  cell_nm * iy + cell_nm - 4000))
        top.shapes(lt).insert(txt)

ly.write(out)
print(f"GDS written: {out} ({nx}x{ny} cells, pitch={pitch}nm line={line}nm)")
