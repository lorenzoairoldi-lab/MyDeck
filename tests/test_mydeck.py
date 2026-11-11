import unittest
from unittest.mock import patch
import copy
import curses

from mydeck.app import Dashboard, Sampler, fit, width
from mydeck.system import Collector
from mydeck.overview import Canvas, build_overview, chart


class Screen:
    def __init__(self, h=34, w=120):
        self.h, self.w = h, w
        self.output = []

    def getmaxyx(self):
        return self.h, self.w

    def erase(self):
        self.output = []

    def addstr(self, y, x, text, attr):
        if not (0 <= y < self.h and 0 <= x < self.w and width(text) <= self.w - x):
            raise AssertionError((y, x, text))
        self.output.append(text)

    def refresh(self):
        pass


class TelemetryTests(unittest.TestCase):
    def test_cpu_excludes_guest_and_uses_deltas(self):
        collector = Collector()
        with patch('mydeck.system.read', return_value='cpu 100 0 100 800 0 0 0 0 50 0'):
            self.assertIsNone(collector.cpu()[0][1])
        with patch('mydeck.system.read', return_value='cpu 150 0 100 850 0 0 0 0 100 0'):
            self.assertEqual(collector.cpu()[0][1], 50)

    def test_missing_systemctl_is_explicit(self):
        collector = Collector()
        with patch('mydeck.system.command', return_value=None):
            collector.slow_data()
        self.assertFalse(collector.slow['services_available'])
        self.assertEqual(collector.slow['services'], [])

    def test_network_rate_uses_counter_delta(self):
        collector = Collector()
        header = 'header\nheader\n'
        def fake_read(path, default=''):
            if str(path) == '/proc/net/dev':
                return header + 'eth0: 1000 1 0 0 0 0 0 0 2000 1 0 0 0 0 0 0'
            return 'up'
        with patch('mydeck.system.read', side_effect=fake_read):
            self.assertIsNone(collector.networks(None)[0]['rx_rate'])
            collector.net_previous['eth0'] = (500, 1000)
            row = collector.networks(2)[0]
        self.assertEqual(row['rx_rate'], 250)
        self.assertEqual(row['tx_rate'], 500)


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = Collector().sample()

    def dashboard(self, screen):
        sampler = Sampler(1)
        sampler.data = self.snapshot
        with patch.object(Dashboard, 'setup'):
            return Dashboard(screen, sampler)

    def test_all_sections_at_supported_sizes(self):
        for h, w in [(16, 70), (24, 94), (34, 120), (50, 180)]:
            screen = Screen(h, w)
            app = self.dashboard(screen)
            for section in range(8):
                with self.subTest(size=(h, w), section=section):
                    app.navigate(section)
                    app.render()
                    self.assertTrue(screen.output)
                    self.assertTrue(app.lines)

    def test_search_sort_pause_and_menu(self):
        app = self.dashboard(Screen())
        app.handle('2')
        app.handle('s')
        self.assertEqual(app.sort, 'memory')
        app.handle('/')
        for char in 'no-match-application-123':
            app.handle(char)
        app.handle('\n')
        app.render()
        self.assertTrue(any('Nessun processo' in line[0] for line in app.lines))
        app.handle('\x1b')
        self.assertEqual(app.query, '')
        app.handle(' ')
        self.assertTrue(app.paused)
        app.handle('m')
        self.assertFalse(app.sidebar)
        self.assertFalse(app.handle('q'))

    def test_too_small_terminal_and_help(self):
        screen = Screen(10, 40)
        app = self.dashboard(screen)
        app.render()
        self.assertTrue(any('Allarga' in line for line in screen.output))
        screen.h, screen.w = 30, 110
        app.handle('h')
        app.render()
        self.assertTrue(any('KEYBOARD' in line[0] for line in app.lines))

    def test_text_cannot_inject_terminal_control_sequences(self):
        self.assertNotIn('\x1b', fit('\x1b[31mRED\ntext', 30))
        self.assertEqual(width(fit('界e\u0301', 6)), 6)

    def test_reference_layout_colors_and_responsive_panels(self):
        for columns in (66, 90, 110, 156):
            canvas = build_overview(self.snapshot, {}, columns, 35)
            lines, spans = canvas.rows()
            text = '\n'.join(row[0] for row in lines)
            for title in ('CPU', 'Memory', 'Disk', 'Network', 'Running Processes', 'Storage', 'Temperature'):
                self.assertIn(title, text)
            self.assertEqual(len(lines), canvas.height)
            for row, runs in spans.items():
                self.assertEqual(width(lines[row][0]), columns)
                self.assertEqual(sum(run[4] for run in runs), columns)
            colors = {cell[1] for row in canvas.cells for cell in row}
            self.assertTrue({'cpu', 'memory', 'disk', 'amber', 'border'}.issubset(colors))
            if columns >= 88:
                self.assertEqual(lines[0][0].count('╭'), 4)

    def test_overview_search_and_mouse_drilldown(self):
        app = self.dashboard(Screen(40, 140))
        app.sampler.data = copy.deepcopy(self.snapshot)
        app.sampler.data['processes'] = [
            {'pid': 1, 'name': 'firefox', 'user': 'me', 'cpu': 20, 'memory': 1000, 'state': 'S'},
            {'pid': 2, 'name': 'python', 'user': 'me', 'cpu': 30, 'memory': 2000, 'state': 'S'},
        ]
        app.query = 'firefox'
        app.render()
        text = '\n'.join(line[0] for line in app.lines)
        self.assertIn('firefox', text)
        self.assertNotIn('python', text)
        self.assertTrue(app.links)
        row, col, _, section = app.links[0]
        mouse = (0, app.side_width + 2 + col, app.content_start + row, 0, curses.BUTTON1_CLICKED)
        with patch('mydeck.app.curses.getmouse', return_value=mouse):
            app.handle(curses.KEY_MOUSE)
        self.assertEqual(app.section, section)

    def test_chart_ignores_expired_and_missing_samples(self):
        canvas = Canvas(20, 4)
        chart(canvas, 0, 0, 4, 20, [(1, 90), (99, None), (100, 50)], 'cpu', 100)
        colored = [(x, y) for y, row in enumerate(canvas.cells) for x, cell in enumerate(row) if cell[1].startswith('cpu')]
        self.assertTrue(colored)
        self.assertTrue(all(x == 19 for x, _ in colored))

    def test_graph_interpolates_only_measured_endpoints(self):
        canvas = Canvas(30, 6)
        chart(canvas, 0, 0, 6, 30, [(98, 0), (100, 100)], 'cpu', 99, window=3)
        curve = [(x, y) for y, row in enumerate(canvas.cells) for x, cell in enumerate(row) if cell[1].startswith('cpu')]
        self.assertTrue(curve)
        self.assertTrue(any(x == 29 and y in (2, 3) for x, y in curve))
        self.assertFalse(any(y == 0 for _, y in curve))
        self.assertFalse(any(x < 19 for x, _ in curve))
        self.assertTrue(all(0x2800 < ord(cell[0]) <= 0x28ff for row in canvas.cells for cell in row if cell[1].startswith('cpu')))

    def test_graph_breaks_at_missing_samples(self):
        canvas = Canvas(30, 6)
        chart(canvas, 0, 0, 6, 30, [(96, 10), (98, None), (100, 90)], 'cpu', 100, window=6)
        curve = [x for row in canvas.cells for x, cell in enumerate(row) if cell[1].startswith('cpu')]
        self.assertTrue(curve)
        self.assertFalse(any(12 < x < 28 for x in curve))

    def test_pause_freezes_graph_clock_and_zoom_is_interactive(self):
        app = self.dashboard(Screen(40, 140))
        app.render()
        app.handle(' ')
        stamp = app.graph_time
        before = list(app.lines)
        with patch('mydeck.app.time.time', return_value=stamp + 30):
            app.render()
        self.assertEqual(app.graph_time, stamp)
        self.assertEqual(app.lines, before)
        app.handle('t')
        self.assertEqual(app.graph_window, 60)
        app.handle('t')
        self.assertEqual(app.graph_window, 15)
        app.handle('t')
        self.assertEqual(app.graph_window, 30)

    def test_nerd_icons_and_palette_restore(self):
        app = self.dashboard(Screen(40, 140))
        app.icons = 'nerd'
        app.render()
        self.assertTrue(any('\uf015' in line for line in app.screen.output))
        app.saved_colors = {234: (1, 2, 3), 235: (4, 5, 6)}
        with patch('mydeck.app.curses.init_color') as init_color:
            app.restore_colors()
        self.assertEqual(init_color.call_count, 2)
        init_color.assert_any_call(234, 1, 2, 3)

    def test_sampler_records_real_timestamped_histories(self):
        sampler = Sampler(1)
        def sample():
            sampler.stop.set()
            sampler.refresh.set()
            return self.snapshot
        with patch('mydeck.app.Collector') as collector:
            collector.return_value.sample.side_effect = sample
            sampler.run()
        self.assertIsNone(sampler.error)
        for key in ('cpu', 'memory', 'disk', 'rx', 'tx'):
            self.assertEqual(sampler.history[key][0][0], self.snapshot['timestamp'])


if __name__ == '__main__':
    unittest.main()
