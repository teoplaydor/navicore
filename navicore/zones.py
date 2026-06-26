"""Predefined snap zones per monitor (RESEARCH.md §7/§11).

A monitor's work area is divided into a grid (3x2 for landscape, 2x3 for portrait by
default). Big targets tolerate the ~3-4cm webcam/head error. Each cell is one zone, e.g.
the top-right cell of a 3x2 grid == "the top-right sixth".
"""
from __future__ import annotations

from dataclasses import dataclass

from .monitors import Monitor

_ROW_NAMES_2 = ["top", "bottom"]
_ROW_NAMES_3 = ["top", "middle", "bottom"]
_COL_NAMES_2 = ["left", "right"]
_COL_NAMES_3 = ["left", "center", "right"]


@dataclass
class Zone:
    monitor_index: int
    col: int
    row: int
    cols: int
    rows: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def label(self) -> str:
        cols = _COL_NAMES_3 if self.cols == 3 else _COL_NAMES_2 if self.cols == 2 else [str(self.col)]
        rows = _ROW_NAMES_3 if self.rows == 3 else _ROW_NAMES_2 if self.rows == 2 else [str(self.row)]
        c = cols[self.col] if self.col < len(cols) else str(self.col)
        r = rows[self.row] if self.row < len(rows) else str(self.row)
        return f"{r}-{c}"


def grid_for(mon: Monitor, cfg) -> tuple[int, int]:
    if mon.is_portrait:
        return cfg.grid_cols_portrait, cfg.grid_rows_portrait
    return cfg.grid_cols_landscape, cfg.grid_rows_landscape


def zones_for_monitor(mon: Monitor, cfg) -> list[Zone]:
    cols, rows = grid_for(mon, cfg)
    # use the work area so windows don't overlap the taskbar
    wl, wt, wr, wb = mon.work_left, mon.work_top, mon.work_right, mon.work_bottom
    w = (wr - wl) / cols
    h = (wb - wt) / rows
    out: list[Zone] = []
    for row in range(rows):
        for col in range(cols):
            left = int(round(wl + col * w))
            top = int(round(wt + row * h))
            right = int(round(wl + (col + 1) * w))
            bottom = int(round(wt + (row + 1) * h))
            out.append(Zone(mon.index, col, row, cols, rows, left, top, right, bottom))
    return out


def zone_at(mon: Monitor, cfg, nx: float, ny: float) -> Zone:
    """Pick the zone at normalized position (nx, ny) in 0..1 within the monitor."""
    cols, rows = grid_for(mon, cfg)
    col = max(0, min(cols - 1, int(nx * cols)))
    row = max(0, min(rows - 1, int(ny * rows)))
    zs = zones_for_monitor(mon, cfg)
    for z in zs:
        if z.col == col and z.row == row:
            return z
    return zs[0]
