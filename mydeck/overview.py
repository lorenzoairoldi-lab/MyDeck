"""Terminal-native panels inspired by the supplied System Monitor reference."""
from __future__ import annotations

from .system import duration, size
from .graphs import chart


UNICODE_ICONS = {
    'Overview': '⌂', 'Processes': '☷', 'Performance': '▥', 'Storage': '▣',
    'Networks': '≋', 'Sensors': '♨', 'Services': '⚙', 'Applications': '▦',
    'CPU': '▦', 'Memory': '▤', 'Disk': '▣', 'Network': '≋', 'app': '◆',
}
NERD_ICONS = {
    'Overview': '\uf015', 'Processes': '\uf03a', 'Performance': '\uf080',
    'Storage': '\uf0a0', 'Networks': '\uf1eb', 'Sensors': '\uf2c9',
    'Services': '\uf013', 'Applications': '\uf00a', 'CPU': '\uf4bc',
    'Memory': '\uefc5', 'Disk': '\uf0a0', 'Network': '\uf1eb', 'app': '\uf2d0',
}
APP_ICONS = {
    'firefox': ('\uf269', 'orange'), 'chrome': ('\uf268', 'amber'),
    'chromium': ('\uf268', 'cpu'), 'spotify': ('\uf1bc', 'disk'),
    'code': ('\ue70c', 'cpu'), 'nautilus': ('\uf07b', 'cpu'),
    'systemd': ('\uf013', 'text'), 'gnome': ('\uf2bd', 'text'),
    'pulse': ('\uf028', 'text'), 'pipewire': ('\uf028', 'text'),
    'xorg': ('\uf108', 'text'), 'python': ('\ue73c', 'amber'),
    'terminal': ('\uf120', 'disk'),
}


def icon(name, style='unicode'):
    return (NERD_ICONS if style == 'nerd' else UNICODE_ICONS).get(name, '◆')


class Canvas:
    """A scrollable cell buffer; spans preserve colors inside each panel."""
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.cells = [[(' ', 'base', False) for _ in range(width)] for _ in range(height)]
        self.links = []

    def text(self, y, x, value, color='text', bold=False, limit=None):
        from .app import clean, width
        end = min(self.width, x + limit) if limit is not None else self.width
        for char in clean(value):
            n = width(char)
            if n == 0:
                continue
            if x + n > end:
                break
            if 0 <= y < self.height and 0 <= x < self.width:
                self.cells[y][x] = (char, color, bold)
                for offset in range(1, n):
                    self.cells[y][x + offset] = ('', color, bold)
            x += n

    def panel(self, y, x, h, w, title, accent='text', action=None):
        for row in range(y, y + h):
            self.text(row, x, ' ' * w, 'panel', limit=w)
        self.text(y, x, '╭' + '─' * (w - 2) + '╮', 'border', limit=w)
        self.text(y + h - 1, x, '╰' + '─' * (w - 2) + '╯', 'border', limit=w)
        for row in range(y + 1, y + h - 1):
            self.text(row, x, '│', 'border', limit=1)
            self.text(row, x + w - 1, '│', 'border', limit=1)
        self.text(y + 1, x + 2, title, accent, True, w - 4)
        if action and w >= len(title) + len(action[0]) + 7:
            label, section = action
            self.text(y + 1, x + w - len(label) - 2, label, 'link', limit=len(label))
            self.links.append((y + 1, x + w - len(label) - 2, len(label), section))

    def rows(self):
        lines, spans = [], {}
        for y, cells in enumerate(self.cells):
            lines.append((''.join(cell[0] for cell in cells), 'base', False))
            runs = []
            start, color, bold, text = 0, cells[0][1], cells[0][2], ''
            for x, (char, next_color, next_bold) in enumerate(cells):
                if (next_color, next_bold) != (color, bold):
                    if text:
                        runs.append((start, text, color, bold, x - start))
                    start, color, bold, text = x, next_color, next_bold, ''
                text += char
            if text:
                runs.append((start, text, color, bold, len(cells) - start))
            spans[y] = runs
        return lines, spans


def sorted_processes(d, query, sort):
    rows = [r for r in d['processes'] if query.casefold() in f"{r['name']} {r['pid']} {r['user']}".casefold()]
    return sorted(rows, key=lambda r: r[sort] if r[sort] is not None else -1,
                  reverse=sort in ('cpu', 'memory'))


def build_overview(d, history, width, viewport, query='', sort='cpu', icons='unicode',
                   now=None, window=30, interval=0.5):
    from .app import fit, percent, pct, rate

    now = d['timestamp'] if now is None else now
    count = 4 if width >= 88 else 2
    card_h, gap = (11 if viewport >= 30 else 10), 1
    top_h = card_h if count == 4 else card_h * 2 + gap
    lower_h = max(15, viewport - top_h - 1)
    split = width >= 76
    total_h = top_h + 1 + (lower_h if split else lower_h + 21)
    canvas = Canvas(width, total_h)
    m = d['memory']
    root = next((r for r in d['storage'] if r['mount'] == '/'), None)
    cpu = dict(d['cpu']).get('cpu')
    memory = percent(m['used'], m['total'])
    disk = percent(root['used'], root['total']) if root else None
    nets = [n for n in d['networks'] if n['name'] != 'lo']
    rx = sum(n['rx_rate'] or 0 for n in nets) if any(n['rx_rate'] is not None for n in nets) else None
    tx = sum(n['tx_rate'] or 0 for n in nets) if any(n['tx_rate'] is not None for n in nets) else None
    cards = [
        ('CPU', cpu, (f"{d['cpu_mhz'] / 1000:.1f} GHz · " if d.get('cpu_mhz') else '') + f"{max(0, len(d['cpu']) - 1)} cores", 'cpu', 'cpu'),
        ('Memory', memory, f"{size(m['used'])} / {size(m['total'])}", 'memory', 'memory'),
        ('Disk', disk, f"{size(root['used'])} / {size(root['total'])}" if root else 'Non disponibile', 'disk', 'disk'),
        ('Network', None, '', 'amber', 'rx'),
    ]
    card_w = (width - (count - 1) * gap) // count
    for i, (title, value, detail, color, key) in enumerate(cards):
        y, x = (i // count) * (card_h + gap), (i % count) * (card_w + gap)
        w = width - x if i % count == count - 1 else card_w
        canvas.panel(y, x, card_h, w, f'{icon(title, icons)}  {title}', color)
        canvas.text(y + 1, x + 5, title, 'text', True, w - 7)
        if title != 'Network':
            label = f'{min(100, max(0, value)):.0f}%' if value is not None else '—'
            canvas.text(y + 3, x + 3, label, 'text', True, w - 6)
            points = [v for t, v in history.get(key, []) if v is not None and t >= now - window]
            if len(points) >= 2 and w >= 23:
                delta = points[-1] - points[-2]
                trend = ('↗' if delta > 0.05 else '↘' if delta < -0.05 else '→') + f' {abs(delta):.1f}'
                canvas.text(y + 3, x + w - 10, trend, color, limit=8)
            canvas.text(y + 4, x + 2, detail, 'panel_muted', limit=w - 4)
            chart(canvas, y + 5, x + 2, card_h - 7, w - 4, history.get(key, []), color, now,
                  window=window, max_gap=max(5, interval * 3))
        else:
            canvas.text(y + 3, x + 2, '↓ ' + rate(rx), 'cpu', True, w - 4)
            canvas.text(y + 4, x + 2, '↑ ' + rate(tx), 'memory', True, w - 4)
            series = list(history.get('rx', [])) + list(history.get('tx', []))
            peak = max((v for t, v in series if v is not None and t >= now - window), default=1)
            chart(canvas, y + 5, x + 2, card_h - 7, w - 4, history.get('rx', []), 'amber', now, peak * 1.2, window=window, max_gap=max(5, interval * 3))
            chart(canvas, y + 5, x + 2, card_h - 7, w - 4, history.get('tx', []), 'memory', now, peak * 1.2, overlay=True, window=window, max_gap=max(5, interval * 3))
        canvas.text(y + card_h - 2, x + 2, f'−{window}s', 'panel_muted', limit=w - 4)
        canvas.text(y + card_h - 2, x + w - 5, 'now', 'panel_muted', limit=3)

    y = top_h + 1
    process_w = (width - 1) * 3 // 5 if split else width
    canvas.panel(y, 0, lower_h, process_w, 'Running Processes', action=('View all [2]', 1))
    canvas.text(y + 2, 2, ('⌕ ' + query if query else '⌕ Search processes…  [/]'), 'panel_muted', limit=process_w - 4)
    name_w = max(8, process_w - 33)
    sizes = [name_w, 8, 11, 7]
    columns = ['Name', 'CPU ' + ('▾' if sort == 'cpu' else ''), 'Memory', 'PID']
    if process_w < 52:
        sizes = [max(8, process_w - 25), 7, 10]
        columns = columns[:3]
    canvas.text(y + 4, 2, ' '.join(fit(v, n) for v, n in zip(columns, sizes)), 'table_header', True, process_w - 4)
    processes = sorted_processes(d, query, sort)
    visible = max(0, lower_h - 7)
    for row, proc in enumerate(processes[:visible]):
        color = 'text'
        glyph = '◆'
        if icons == 'nerd':
            glyph = NERD_ICONS['app']
            for name, (app_icon, app_color) in APP_ICONS.items():
                if name in proc['name'].casefold():
                    glyph, color = app_icon, app_color
                    break
        values = [f"  {proc['name']}", pct(proc['cpu']).strip(), size(proc['memory']), str(proc['pid'])]
        canvas.text(y + 5 + row, 2, ' '.join(fit(v, n) for v, n in zip(values, sizes)), 'text', limit=process_w - 4)
        canvas.text(y + 5 + row, 2, glyph, color, limit=1)
    if not processes:
        canvas.text(y + 6, 2, 'Nessun processo corrispondente.', 'panel_muted', limit=process_w - 4)
    canvas.text(y + lower_h - 2, 2, f'{len(processes)} processes · sort [s] · uptime {duration(d["uptime"])}', 'panel_muted', limit=process_w - 4)

    right_x = process_w + 1 if split else 0
    right_y = y if split else y + lower_h + 1
    right_w = width - right_x
    storage_h = max(8, lower_h - 9) if split else 11
    canvas.panel(right_y, right_x, storage_h, right_w, 'Storage', action=('View all [4]', 3))
    mounts = sorted(d['storage'], key=lambda r: (r['mount'] != '/', r['mount'] not in ('/', '/home', '/boot'), r['mount'].startswith('/snap'), r['mount']))
    visible_mounts = max(1, (storage_h - 4) // 3)
    for i, mount in enumerate(mounts[:visible_mounts]):
        row = right_y + 3 + i * 3
        used = percent(mount['used'], mount['total'])
        color = ['cpu', 'memory', 'disk'][i % 3]
        label = '/ (root)' if mount['mount'] == '/' else mount['mount']
        canvas.text(row, right_x + 2, f'{icon("Disk", icons)}  {label}', 'text', True, right_w - 11)
        canvas.text(row, right_x + right_w - 7, f'{used:.0f}%', 'text', True, 5)
        canvas.text(row + 1, right_x + 5, f'{size(mount["used"])} / {size(mount["total"])}', 'panel_muted', limit=right_w - 7)
        length = max(1, right_w - 8)
        fill = round(min(100, max(0, used)) / 100 * length)
        canvas.text(row + 2, right_x + 5, '━' * length, 'track', limit=length)
        canvas.text(row + 2, right_x + 5, '━' * fill, 'warning_panel' if used >= 90 else color, limit=fill)
    if not mounts:
        canvas.text(right_y + 3, right_x + 2, 'Nessun filesystem disponibile.', 'panel_muted', limit=right_w - 4)

    temp_y = right_y + storage_h + 1
    temp_h = (lower_h - storage_h - 1) if split else 8
    canvas.panel(temp_y, right_x, temp_h, right_w, 'Temperature', action=('View all [6]', 5))
    temps = [s for s in d['sensors'] if s['kind'] == 'temp']
    # Prefer actual CPU / GPU / system sensors when those are exposed. Never
    # relabel a disk or a thermal-zone sensor as a GPU to match the mockup.
    def group(sensor):
        name = (sensor['chip'] + ' ' + sensor['name']).lower()
        if any(v in name for v in ('package', 'tctl', 'cpu', 'coretemp', 'k10temp')):
            return 0, 'CPU'
        if any(v in name for v in ('gpu', 'amdgpu', 'nouveau', 'radeon', 'nvidia')):
            return 1, 'GPU'
        if any(v in name for v in ('acpitz', 'pch', 'asus', 'nct', 'it87', 'thinkpad', 'system')):
            return 2, 'System'
        return 3, sensor['chip']
    temps.sort(key=group)
    chosen, chips = [], set()
    for sensor in temps:
        identity = group(sensor)[1]
        if identity not in chips:
            chosen.append(sensor)
            chips.add(identity)
        if len(chosen) == 3:
            break
    sensor_w = (right_w - 4) // max(1, len(chosen))
    for i, sensor in enumerate(chosen):
        sx = right_x + 2 + i * sensor_w
        color = ['red', 'amber', 'disk'][i % 3]
        canvas.text(temp_y + 3, sx, group(sensor)[1], 'panel_muted', limit=sensor_w - 1)
        canvas.text(temp_y + 4, sx, f'{sensor["value"]:.0f}°C', color, True, sensor_w - 1)
        key = 'sensor:' + sensor.get('id', sensor['chip'] + ':' + sensor['name'])
        if temp_h >= 8:
            chart(canvas, temp_y + 5, sx, temp_h - 6, sensor_w - 1, history.get(key, []), color, now, window=window, max_gap=max(5, interval * 3))
    if not chosen:
        canvas.text(temp_y + 3, right_x + 2, 'Sensori non disponibili.', 'panel_muted', limit=right_w - 4)
    return canvas
