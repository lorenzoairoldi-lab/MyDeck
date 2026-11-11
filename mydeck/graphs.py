"""Subcell line graphs: two horizontal by four vertical pixels per cell."""
from __future__ import annotations

import math

DOTS = ((1, 8), (2, 16), (4, 32), (64, 128))


def _line(x0, y0, x1, y1):
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for step in range(steps + 1):
        amount = step / steps
        yield round(x0 + (x1 - x0) * amount), round(y0 + (y1 - y0) * amount)


def chart(canvas, y, x, h, w, series, color, now, maximum=100,
          overlay=False, window=30, max_gap=5):
    """Draw measured samples and linear segments bounded by measured endpoints.

    A moving time axis and a slightly delayed presentation clock let the next
    measured sample enter continuously. Missing samples break the line. No
    values are extrapolated into the future or before the first sample.
    """
    if w < 1 or h < 1 or window <= 0:
        return
    if not overlay:
        for row in range(h):
            canvas.text(y + row, x, ' ' * w, 'panel', limit=w)
        for col in range(0, w, 6):
            canvas.text(y + h - 1, x + col, '·', 'grid', limit=1)
    pw, ph = w * 2, h * 4
    start, scale = now - window, max(1, maximum)
    pixels = set()
    last = None
    head = None

    def project(stamp, value):
        px = round((stamp - start) / window * (pw - 1))
        py = round((1 - min(1, max(0, value / scale))) * (ph - 1))
        return px, py

    for stamp, value in sorted(series, key=lambda point: point[0]):
        if value is None or not math.isfinite(value):
            last = None
            continue
        if last is not None:
            before_t, before_v = last
            if 0 < stamp - before_t <= max_gap:
                left, right = max(start, before_t), min(now, stamp)
                if left <= right:
                    a = before_v + (value - before_v) * (left - before_t) / (stamp - before_t)
                    b = before_v + (value - before_v) * (right - before_t) / (stamp - before_t)
                    p0, p1 = project(left, a), project(right, b)
                    pixels.update(_line(*p0, *p1))
                    head = p1
        if start <= stamp <= now:
            head = project(stamp, value)
            pixels.add(head)
        last = (stamp, value)

    masks = {}
    for px, py in pixels:
        if 0 <= px < pw and 0 <= py < ph:
            cell = (px // 2, py // 4)
            masks[cell] = masks.get(cell, 0) | DOTS[py % 4][px % 2]
    for (col, row), mask in masks.items():
        if not (0 <= y + row < canvas.height and 0 <= x + col < canvas.width):
            continue
        style = color
        previous, previous_color, _ = canvas.cells[y + row][x + col]
        if overlay and previous and 0x2800 < ord(previous) <= 0x28ff:
            mask |= ord(previous) - 0x2800
            if previous_color not in ('grid', 'panel', color, color + '_head'):
                style = 'graph_mix'
        if head and (head[0] // 2, head[1] // 4) == (col, row) and style != 'graph_mix':
            style = color + '_head' if now % 1 < 0.5 else color
        canvas.text(y + row, x + col, chr(0x2800 + mask), style, limit=1)
