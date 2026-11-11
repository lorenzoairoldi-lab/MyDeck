"""Responsive curses UI for MyDeck."""
from __future__ import annotations

import argparse
from collections import deque
import curses
import datetime
import json
import locale
import os
import sys
import threading
import time
import unicodedata

from .system import Collector, duration, size
from .overview import Canvas, build_overview, icon
from .graphs import chart

VERSION = '1.0.0'
SECTIONS = ['Overview', 'Processes', 'Performance', 'Storage', 'Networks', 'Sensors', 'Services', 'Applications']
DESCRIPTIONS = [
    'Il tuo sistema, a colpo d’occhio.', 'Processi attivi e risorse utilizzate.',
    'CPU, memoria e attività dei dischi in tempo reale.', 'Filesystem montati e spazio disponibile.',
    'Interfacce, indirizzi e traffico di rete.', 'Temperature, ventole e sensori hardware.',
    'Stato dei servizi di sistema.', 'Applicazioni desktop installate.']


def clean(value):
    return ''.join(c if c.isprintable() or unicodedata.category(c) == 'Co' else ' ' for c in str(value))


def width(value):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in value)


def fit(value, length):
    result, used = '', 0
    for c in clean(value):
        n = width(c)
        if used + n > max(0, length):
            break
        result += c
        used += n
    return result + ' ' * max(0, length - used)


def percent(used, total):
    return used / total * 100 if total else 0


def pct(value):
    return f'{value:5.1f}%' if value is not None else '   —  '


def rate(value):
    return size(value) + '/s' if value is not None else 'campionamento…'


def bar(value, length=20):
    if value is None:
        return '░' * length
    filled = round(max(0, min(100, value)) / 100 * length)
    return '━' * filled + '·' * (length - filled)


def spark(values, length=50):
    levels = '▁▂▃▄▅▆▇█'
    items = list(values)[-length:]
    return ''.join(levels[min(7, max(0, round(v / 100 * 7)))] for v in items).rjust(length, '·')


class Sampler:
    def __init__(self, interval):
        self.interval = interval
        self.data = None
        self.error = None
        self.history = {key: deque(maxlen=240) for key in ('cpu', 'memory', 'disk', 'rx', 'tx')}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.refresh = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name='mydeck-telemetry')

    def run(self):
        collector = Collector()
        while not self.stop.is_set():
            try:
                data = collector.sample()
                with self.lock:
                    self.data, self.error = data, None
                    memory = data['memory']
                    root = next((r for r in data['storage'] if r['mount'] == '/'), None)
                    nets = [n for n in data['networks'] if n['name'] != 'lo']
                    values = {'cpu': dict(data['cpu']).get('cpu'),
                              'memory': percent(memory['used'], memory['total']),
                              'disk': percent(root['used'], root['total']) if root else None}
                    for direction in ('rx', 'tx'):
                        values[direction] = sum(n[direction + '_rate'] or 0 for n in nets) if any(n[direction + '_rate'] is not None for n in nets) else None
                    for sensor in data['sensors']:
                        if sensor['kind'] == 'temp':
                            key = 'sensor:' + sensor.get('id', sensor['chip'] + ':' + sensor['name'])
                            values[key] = sensor['value']
                    for key, value in values.items():
                        self.history.setdefault(key, deque(maxlen=240)).append((data['timestamp'], value))
                    for key in list(self.history):
                        if key not in values:
                            del self.history[key]
            except Exception as error:
                with self.lock:
                    self.error = str(error)
            self.refresh.wait(self.interval)
            self.refresh.clear()


class Dashboard:
    def __init__(self, screen, sampler, icons='unicode'):
        self.screen, self.sampler = screen, sampler
        self.section = 0
        self.sidebar = True
        self.side_width = 0
        self.expanded = True
        self.offset = 0
        self.query = ''
        self.searching = False
        self.paused = False
        self.help = False
        self.sort = 'cpu'
        self.data = None
        self.history = {'cpu': [], 'memory': []}
        self.lines = []
        self.colors = {}
        self.icons = icons
        self.spans = {}
        self.links = []
        self.menu_rows = {}
        self.viewport = 0
        self.content_start = 5
        self.saved_colors = {}
        self.graph_time = None
        self.graph_window = 30
        self.setup()

    def setup(self):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.screen.keypad(True)
        self.screen.timeout(150)
        if curses.has_colors():
            curses.start_color()
            # The reference uses a tinted navy background, not neutral gray.
            # Save every modified palette entry and restore it before endwin.
            if curses.COLORS >= 256 and curses.can_change_color():
                reference = {234: '#09131b', 235: '#101d25', 236: '#0b151e',
                             237: '#0d1922', 238: '#24363f', 26: '#373b83',
                             45: '#25c7f5', 135: '#a933ef', 77: '#35de68',
                             179: '#e6ad45', 255: '#e3edf2', 245: '#81939f'}
                for number, value in reference.items():
                    try:
                        self.saved_colors[number] = curses.color_content(number)
                        rgb = [round(int(value[n:n + 2], 16) / 255 * 1000) for n in (1, 3, 5)]
                        curses.init_color(number, *rgb)
                    except curses.error:
                        pass
            palette = {
                'base': (255, 234), 'muted': (245, 234), 'blue': (45, 234),
                'cyan': (45, 234), 'good': (77, 234), 'warning': (179, 234),
                'bad': (203, 234), 'side': (255, 236), 'selected': (255, 26),
                'side_muted': (245, 236), 'header': (255, 237),
                'panel': (255, 235), 'text': (255, 235), 'panel_muted': (245, 235),
                'border': (238, 235), 'cpu': (45, 235), 'memory': (135, 235),
                'disk': (77, 235), 'amber': (179, 235), 'red': (203, 235),
                'orange': (208, 235), 'link': (69, 235), 'grid': (238, 235),
                'track': (240, 235), 'table_header': (255, 238), 'warning_panel': (179, 235),
                'cpu_fill': (24, 235), 'memory_fill': (54, 235),
                'disk_fill': (22, 235), 'amber_fill': (58, 235), 'red_fill': (52, 235),
                'live_header': (77, 237), 'paused_header': (179, 237),
                'cpu_head': (87, 235), 'memory_head': (177, 235),
                'disk_head': (120, 235), 'amber_head': (223, 235),
                'red_head': (217, 235), 'graph_mix': (255, 235),
            }
            fallback = {
                'base': (7, 0), 'muted': (7, 0), 'blue': (4, 0), 'cyan': (6, 0),
                'good': (2, 0), 'warning': (3, 0), 'bad': (1, 0),
                'side': (7, 4), 'selected': (7, 4), 'side_muted': (6, 4), 'header': (7, 4),
            }
            for name in palette:
                if name not in fallback:
                    fg = 6 if name.startswith('cpu') else 5 if name.startswith('memory') else 2 if name.startswith('disk') else 3 if name.startswith('amber') else 1 if name.startswith('red') else 7
                    fallback[name] = (fg, 0)
            for index, (name, colors) in enumerate((palette if curses.COLORS >= 256 else fallback).items(), 1):
                curses.init_pair(index, *colors)
                self.colors[name] = curses.color_pair(index)
        self.screen.bkgd(' ', self.colors.get('base', 0))
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
        except curses.error:
            pass

    def restore_colors(self):
        for number, rgb in self.saved_colors.items():
            try:
                curses.init_color(number, *rgb)
            except curses.error:
                pass

    def draw(self, y, x, text, color='base', bold=False, length=None):
        h, w = self.screen.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        available = min(w - x, length if length is not None else w - x)
        attr = self.colors.get(color, 0) | (curses.A_BOLD if bold else 0)
        try:
            self.screen.addstr(y, x, fit(text, available), attr)
        except curses.error:
            pass  # Writing the lower-right cell can return ERR after drawing.

    def line(self, text='', color='base', bold=False):
        self.lines.append((text, color, bold))

    def heading(self, title, detail=''):
        self.line()
        self.line(title.upper() + (f'  /  {detail}' if detail else ''), 'cyan', True)
        self.line('─' * self.content_width, 'muted')

    def table(self, columns, rows):
        # columns: (label, width), last column stretches to fit.
        remaining = self.content_width
        sizes = []
        for _, requested in columns[:-1]:
            n = min(requested, max(3, remaining - 12))
            sizes.append(n)
            remaining -= n + 2
        sizes.append(max(1, remaining))
        self.line('  '.join(fit(label, n) for (label, _), n in zip(columns, sizes)), 'muted', True)
        for row in rows:
            values, color = row if isinstance(row, tuple) else (row, 'base')
            self.line('  '.join(fit(value, n) for value, n in zip(values, sizes)), color)

    def matches(self, *values):
        return self.query.casefold() in ' '.join(str(v) for v in values).casefold()

    def overview(self, d):
        canvas = build_overview(d, self.history, self.content_width,
                                self.viewport,
                                self.query, self.sort, self.icons,
                                now=self.graph_time, window=self.graph_window,
                                interval=self.sampler.interval)
        self.lines, self.spans = canvas.rows()
        self.links = canvas.links

    def processes(self, d):
        rows = [r for r in d['processes'] if self.matches(r['name'], r['pid'], r['user'])]
        rows.sort(key=lambda r: r[self.sort] if r[self.sort] is not None else -1,
                  reverse=self.sort in ('cpu', 'memory'))
        self.line(f"{len(rows)} processi   ·   ordina: {self.sort.upper()} [s]   ·   filtra [/]")
        self.line('CPU per processo: 100% = un core; può superare il 100%.', 'muted')
        self.line()
        self.table([('PID', 7), ('USER', 12), ('CPU %', 7), ('RAM', 11), ('S', 2), ('PROCESS', 0)],
                   [[r['pid'], r['user'], pct(r['cpu']), size(r['memory']), r['state'], r['name']] for r in rows])
        if not rows:
            self.line('Nessun processo corrispondente.', 'muted')

    def performance(self, d):
        canvas = Canvas(self.content_width, 12)
        widths = [(self.content_width - 1) // 2, self.content_width // 2]
        for i, (title, key, color, value) in enumerate([
                ('CPU', 'cpu', 'cpu', dict(d['cpu']).get('cpu')),
                ('Memory', 'memory', 'memory', percent(d['memory']['used'], d['memory']['total']))]):
            x = 0 if i == 0 else widths[0] + 1
            w = widths[i]
            canvas.panel(0, x, 12, w, icon(title, self.icons) + '  ' + title, color)
            canvas.text(3, x + 2, pct(value), 'text', True, w - 4)
            chart(canvas, 5, x + 2, 5, w - 4, self.history.get(key, []), color,
                  self.graph_time or d['timestamp'], window=self.graph_window,
                  max_gap=max(5, self.sampler.interval * 3))
            canvas.text(10, x + 2, f'−{self.graph_window}s   ·   now', 'panel_muted', limit=w - 4)
        self.lines, self.spans = canvas.rows()
        self.heading('CPU', f"{max(0, len(d['cpu']) - 1)} core logici")
        for name, value in d['cpu']:
            self.line(f"{name.upper():8} {pct(value)}  {bar(value, max(8, min(54, self.content_width - 19)))}", 'blue')
        self.heading('Memory')
        m = d['memory']
        for title, used, total in [('RAM', m['used'], m['total']), ('SWAP', m['swap_used'], m['swap_total'])]:
            self.line(f"{title:8} {size(used)} / {size(total)}    {pct(percent(used, total))}", 'cyan')
        self.line(f"Disponibile: {size(m['available'])}     Cache: {size(m['cached'])}", 'muted')
        self.heading('Disk I/O')
        self.table([('DEVICE', 18), ('READ / s', 20), ('WRITE / s', 0)],
                   [[r['name'], rate(r['read']), rate(r['write'])] for r in d['disk_io']])
        if not d['disk_io']:
            self.line('Statistiche I/O non disponibili.', 'muted')

    def storage(self, d):
        rows = [r for r in d['storage'] if self.matches(r['mount'], r['device'], r['type'])]
        for disk in rows:
            used = percent(disk['used'], disk['total'])
            self.heading(disk['mount'], disk['type'])
            self.line(disk['device'], 'muted')
            self.line(f"{bar(used, max(8, min(50, self.content_width - 10)))}  {pct(used)}", 'warning' if used >= 90 else 'blue')
            self.line(f"Usato {size(disk['used'])}   ·   Libero {size(disk['free'])}   ·   Totale {size(disk['total'])}")
        if not rows:
            self.line('Nessun filesystem corrispondente disponibile.', 'muted')
        self.line()
        self.line('Filesystem virtuali e mount duplicati esclusi.', 'muted')

    def networks(self, d):
        rows = [r for r in d['networks'] if self.matches(r['name'], r['ip'], r['state'])]
        for net in rows:
            self.heading(net['name'], net['state'])
            self.line(f"IP     {net['ip']}")
            self.line(f"MAC    {net['address']}", 'muted')
            self.line(f"↓ RX   {rate(net['rx_rate']):22}  Totale: {size(net['rx'])}", 'cyan')
            self.line(f"↑ TX   {rate(net['tx_rate']):22}  Totale: {size(net['tx'])}", 'blue')
            self.line(f"Errori RX + TX: {net['errors']}", 'warning' if net['errors'] else 'muted')
        if not rows:
            self.line('Nessuna interfaccia corrispondente.', 'muted')
        self.line()
        self.line('Totali dall’avvio dell’interfaccia; velocità tra due campioni.', 'muted')

    def sensors(self, d):
        rows = [r for r in d['sensors'] if self.matches(r['chip'], r['name'], r['kind'])]
        self.line('Lettura diretta dei sensori esposti dal kernel.', 'muted')
        self.line()
        self.table([('CHIP', 18), ('SENSOR', 24), ('VALUE', 13), ('LIMIT', 0)],
                   [([r['chip'], r['name'], f"{r['value']:.1f} {r['unit']}",
                      f"{r['limit']:.1f} {r['unit']}" if r['limit'] is not None else '—'],
                     'warning' if r['limit'] is not None and r['value'] >= r['limit'] else 'base') for r in rows])
        if not rows:
            self.line('Nessun sensore disponibile o corrispondente.', 'muted')
            self.line('L’hardware deve esporre sensori leggibili in /sys/class/hwmon.', 'muted')

    def services(self, d):
        if not d['services_available']:
            self.line('Servizi non disponibili: systemd non attivo o accesso negato.', 'warning')
            return
        rows = [r for r in d['services'] if self.matches(*r.values())]
        rows.sort(key=lambda r: (r['active'] != 'failed', r['active'] != 'active', r['name']))
        self.line(f"{len(rows)} servizi   ·   {sum(r['active'] == 'active' for r in rows)} attivi   ·   {sum(r['active'] == 'failed' for r in rows)} failed")
        self.line('Servizi di sistema caricati · aggiornamento ogni 15 s · sola lettura', 'muted')
        self.line()
        self.table([('SERVICE', 32), ('STATE', 11), ('SUBSTATE', 12), ('DESCRIPTION', 0)],
                   [([r['name'], r['active'], r['sub'], r['description']],
                     'bad' if r['active'] == 'failed' else 'good' if r['active'] == 'active' else 'muted') for r in rows])
        if not rows:
            self.line('Nessun servizio corrispondente.', 'muted')

    def applications(self, d):
        rows = [r for r in d['applications'] if self.matches(*r.values())]
        self.line(f'{len(rows)} applicazioni desktop   ·   filtra [/]')
        self.line('Catalogo .desktop XDG · aggiornamento ogni 15 s · sola lettura', 'muted')
        self.line()
        self.table([('APPLICATION', 28), ('DESCRIPTION', 0)], [[r['name'], r['description'] or r['exec']] for r in rows])
        if not rows:
            self.line('Nessuna applicazione desktop disponibile o corrispondente.', 'muted')

    def render(self):
        self.screen.erase()
        h, w = self.screen.getmaxyx()
        if h < 16 or w < 70:
            self.side_width = 0
            self.draw(1, 2, 'MYDECK', 'blue', True)
            self.draw(3, 2, 'Allarga il terminale: minimo 70 colonne × 16 righe.')
            self.draw(5, 2, '[q] esci')
            self.screen.refresh()
            return
        if not self.paused:
            with self.sampler.lock:
                self.data = self.sampler.data
                self.history = {key: list(value) for key, value in self.sampler.history.items()}
            self.graph_time = time.time() - min(self.sampler.interval, 1)
        with self.sampler.lock:
            error = self.sampler.error
        side = 22 if self.sidebar and w >= 94 else 0
        self.side_width = side
        x = side + 2
        self.content_width = w - x - 2
        is_overview = self.section == 0 and not self.help
        self.content_start = 2 if is_overview else 5
        self.viewport = h - self.content_start - 3
        self.draw(0, 0, '  ' + icon('Performance', self.icons) + '  System Monitor  ·  MyDeck', 'header', True)
        status = '● Paused' if self.paused else '● Error' if error else '● Real-time  ▾'
        self.draw(0, w - 22, status, 'paused_header' if self.paused or error else 'live_header', True, 20)
        self.draw(1, 0, '─' * w, 'muted')
        self.menu_rows = {}
        if side:
            for y in range(2, h - 1):
                self.draw(y, 0, '', 'side', length=side)
                self.draw(y, side - 1, '│', 'side_muted', length=1)
            self.draw(2, 2, ('▾' if self.expanded else '▸') + ' MYDECK', 'side_muted', True, side - 4)
            if self.expanded:
                step = 2 if h >= 26 else 1
                for i, title in enumerate(SECTIONS):
                    row = 4 + i * step
                    self.menu_rows[row] = i
                    prefix = '▌' if i == self.section else ' '
                    self.draw(row, 1, f'{prefix} {icon(title, self.icons)}  {title}', 'selected' if i == self.section else 'side', i == self.section, side - 3)
            if h >= 26:
                self.draw(h - 6, 2, 'LOCAL TELEMETRY', 'side_muted', length=side - 4)
                self.draw(h - 4, 2, '[m] menu  [←/→] ▾', 'side_muted', length=side - 4)
                self.draw(h - 3, 2, 'v' + VERSION, 'side_muted', length=side - 4)
        if not is_overview:
            self.draw(2, x, icon(SECTIONS[self.section], self.icons) + '  ' + SECTIONS[self.section], 'blue', True)
            self.draw(3, x, DESCRIPTIONS[self.section], 'muted')
            self.draw(4, x, '─' * self.content_width, 'muted')
        self.lines, self.spans, self.links = [], {}, []
        if self.help:
            self.heading('Keyboard shortcuts')
            for text in [
                '1–8       Apri direttamente una sezione',
                '↑ / ↓     Sezione precedente / successiva',
                'Tab       Sezione successiva',
                '← / →     Chiudi / espandi il menu a tendina',
                'm         Mostra / nascondi barra laterale',
                'j / k     Scorri il contenuto',
                'PgDn/PgUp Scorri una pagina',
                'Home/End  Inizio / fine della pagina',
                '/         Cerca nella sezione (Invio conferma)',
                'Esc       Cancella ricerca / chiudi aiuto',
                's         Ordina processi: CPU, RAM, PID, nome',
                't         Zoom temporale grafici: 15 / 30 / 60 secondi',
                'Spazio    Pausa / riprendi la visualizzazione',
                'r         Richiedi un aggiornamento dei dati',
                'h / ?     Mostra / chiudi questo aiuto',
                'q         Esci da MyDeck',
            ]:
                self.line(text)
        elif self.data:
            if error:
                self.line('Ultimi dati disponibili · errore: ' + error, 'warning')
            getattr(self, SECTIONS[self.section].lower())(self.data)
        else:
            self.line('Raccolta dei dati del sistema…', 'cyan')
            if error:
                self.line('Errore: ' + error, 'bad')
        viewport = self.viewport
        self.offset = max(0, min(self.offset, max(0, len(self.lines) - viewport)))
        for i, (text, color, bold) in enumerate(self.lines[self.offset:self.offset + viewport]):
            spans = self.spans.get(self.offset + i)
            if spans:
                for dx, value, span_color, span_bold, length in spans:
                    self.draw(self.content_start + i, x + dx, value, span_color, span_bold, length)
            else:
                self.draw(self.content_start + i, x, text, color, bold, self.content_width)
        if len(self.lines) > viewport:
            self.draw(h - 3, x, f'↕ {self.offset + 1}–{min(len(self.lines), self.offset + viewport)} / {len(self.lines)}   [j/k · PgUp/PgDn]', 'muted')
        if self.searching or self.query:
            self.draw(h - 2, x, ('CERCA: ' if self.searching else 'FILTRO: ') + self.query + ('▏' if self.searching else '   [Esc cancella]'), 'cyan')
        else:
            host = self.data['host'] if self.data else 'Linux'
            sample = datetime.datetime.fromtimestamp(self.data['timestamp']).strftime('%H:%M:%S') if self.data else '—'
            self.draw(h - 2, x, f'{host}  ·  campione {sample}  ·  {self.sampler.interval:g}s  ·  grafici {self.graph_window}s [t]', 'muted')
        footer = '  1–8 sezioni   ↑↓ naviga   / cerca   SPACE pausa   m menu   h aiuto   q esci' if w >= 94 else '  1–8 sezioni  ↑↓ naviga  / cerca  SPACE pausa  h aiuto  q esci'
        self.draw(h - 1, 0, footer, 'header')
        self.screen.refresh()

    def navigate(self, section):
        self.section = section % len(SECTIONS)
        self.offset, self.query, self.help = 0, '', False

    def handle(self, key):
        if self.searching:
            if key in ('\n', '\r', curses.KEY_ENTER):
                self.searching = False
            elif key == '\x1b':
                self.searching, self.query = False, ''
            elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
                self.query = self.query[:-1]
            elif isinstance(key, str) and key.isprintable() and len(self.query) < 120:
                self.query += key
            self.offset = 0
            return True
        if key in ('q', 'Q', '\x03'):
            return False
        if isinstance(key, str) and key in '12345678':
            self.navigate(int(key) - 1)
        elif key in (curses.KEY_DOWN, '\t'):
            self.navigate(self.section + 1)
        elif key in (curses.KEY_UP, curses.KEY_BTAB):
            self.navigate(self.section - 1)
        elif key == curses.KEY_LEFT:
            self.expanded = False
        elif key == curses.KEY_RIGHT:
            self.expanded = True
        elif key == 'm':
            self.sidebar = not self.sidebar
        elif key in ('h', '?'):
            self.help = not self.help
            self.offset = 0
        elif key == ' ':
            self.paused = not self.paused
        elif key == 'r':
            self.sampler.refresh.set()
        elif key == 't':
            windows = [15, 30, 60]
            self.graph_window = windows[(windows.index(self.graph_window) + 1) % len(windows)]
        elif key == '/':
            self.searching, self.help, self.offset = True, False, 0
        elif key == '\x1b':
            self.query, self.help, self.offset = '', False, 0
        elif key == 's' and self.section in (0, 1):
            sorts = ['cpu', 'memory', 'pid', 'name']
            self.sort = sorts[(sorts.index(self.sort) + 1) % len(sorts)]
        elif key in ('j', curses.KEY_NPAGE):
            self.offset += 1 if key == 'j' else max(1, self.screen.getmaxyx()[0] - 10)
        elif key in ('k', curses.KEY_PPAGE):
            self.offset -= 1 if key == 'k' else max(1, self.screen.getmaxyx()[0] - 10)
        elif key == curses.KEY_HOME:
            self.offset = 0
        elif key == curses.KEY_END:
            self.offset = len(self.lines)
        elif key == curses.KEY_MOUSE:
            try:
                _, mx, my, _, state = curses.getmouse()
                if state & curses.BUTTON1_CLICKED:
                    if my == 0 and mx >= self.screen.getmaxyx()[1] - 22:
                        self.paused = not self.paused
                    elif self.side_width and mx < self.side_width:
                        if my == 2:
                            self.expanded = not self.expanded
                        elif my in self.menu_rows:
                            self.navigate(self.menu_rows[my])
                    else:
                        content_x = self.side_width + 2
                        for row, col, length, section in self.links:
                            if my == self.content_start + row - self.offset and content_x + col <= mx < content_x + col + length:
                                self.navigate(section)
                                break
                elif state & getattr(curses, 'BUTTON4_PRESSED', 0):
                    self.offset -= 3
                elif state & getattr(curses, 'BUTTON5_PRESSED', 0):
                    self.offset += 3
            except curses.error:
                pass
        return True

    def run(self):
        while True:
            started = time.monotonic()
            self.render()
            animated = self.section in (0, 2) and not self.paused and not self.help
            frame = 1 / 20 if animated else 0.15
            self.screen.timeout(max(1, round((frame - (time.monotonic() - started)) * 1000)))
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if not self.handle(key):
                break


def main():
    parser = argparse.ArgumentParser(description='MyDeck — dashboard Linux nel terminale, senza browser.')
    parser.add_argument('--version', action='version', version=f'MyDeck {VERSION}')
    parser.add_argument('--interval', type=float, default=0.5, metavar='SECONDS', help='intervallo di campionamento (default: 0.5)')
    parser.add_argument('--icons', choices=('unicode', 'nerd'), default=os.environ.get('MYDECK_ICONS', 'unicode'), help='icone standard o Nerd Font (default: unicode)')
    parser.add_argument('--once', action='store_true', help='stampa un campione JSON e termina')
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('MyDeck richiede Linux (/proc e /sys).')
    if not 0.25 <= args.interval <= 60:
        parser.error('--interval deve essere compreso tra 0.25 e 60 secondi.')
    if args.once:
        collector = Collector()
        collector.sample()
        time.sleep(args.interval)
        print(json.dumps(collector.sample(), ensure_ascii=False, indent=2))
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('apri MyDeck in un terminale interattivo; usa --once per un campione JSON.')
    locale.setlocale(locale.LC_ALL, '')
    sampler = Sampler(args.interval)
    sampler.thread.start()
    try:
        def run(screen):
            dashboard = Dashboard(screen, sampler, args.icons)
            try:
                dashboard.run()
            finally:
                dashboard.restore_colors()
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
    except curses.error as error:
        print(f'MyDeck: terminale non supportato ({error}). Controlla la variabile TERM.', file=sys.stderr)
        raise SystemExit(1)
    finally:
        sampler.stop.set()
        sampler.refresh.set()
        sampler.thread.join(timeout=0.2)


if __name__ == '__main__':
    main()
