"""Read-only Linux telemetry. No root access or third-party packages."""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import time


def read(path, default=""):
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return default


def command(args, timeout=3):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"})
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def size(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}"
        value /= 1024


def duration(seconds):
    minutes = max(0, int(seconds)) // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return f"{days}d {hours:02}h {minutes:02}m" if days else f"{hours:02}h {minutes:02}m"


class Collector:
    def __init__(self):
        self.cpu_previous = {}
        self.process_previous = {}
        self.net_previous = {}
        self.disk_previous = {}
        self.last_time = None
        self.slow_time = -float("inf")
        self.slow = {"services": [], "services_available": False, "applications": [], "addresses": {}}
        self.ticks = os.sysconf("SC_CLK_TCK")
        self.page_size = os.sysconf("SC_PAGE_SIZE")
        self.users = {}

    def cpu(self):
        result = []
        current = {}
        for line in read('/proc/stat').splitlines():
            parts = line.split()
            if not parts or not re.fullmatch(r"cpu\d*", parts[0]):
                continue
            # guest / guest_nice are already counted in user / nice.
            values = list(map(int, parts[1:9]))
            total, idle = sum(values), values[3] + values[4]
            old = self.cpu_previous.get(parts[0])
            percent = None
            if old and total > old[0]:
                percent = max(0, min(100, 100 * (1 - (idle - old[1]) / (total - old[0]))))
            result.append((parts[0], percent))
            current[parts[0]] = (total, idle)
        self.cpu_previous = current
        return result

    def memory(self):
        info = {}
        for line in read('/proc/meminfo').splitlines():
            key, value = line.split(':', 1)
            info[key] = int(value.split()[0]) * 1024
        total = info.get('MemTotal', 0)
        available = info.get('MemAvailable', info.get('MemFree', 0))
        return {"total": total, "used": total - available,
                "available": available, "cached": info.get('Cached', 0),
                "swap_total": info.get('SwapTotal', 0),
                "swap_used": info.get('SwapTotal', 0) - info.get('SwapFree', 0)}

    def frequency(self):
        values = []
        for line in read('/proc/cpuinfo').splitlines():
            if line.startswith('cpu MHz'):
                try:
                    values.append(float(line.split(':', 1)[1]))
                except ValueError:
                    continue
        return sum(values) / len(values) if values else None

    def processes(self, elapsed):
        rows, current = [], {}
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / 'stat').read_text()
                name = stat[stat.index('(') + 1:stat.rindex(')')]
                values = stat[stat.rindex(')') + 2:].split()
                ticks = int(values[11]) + int(values[12])
                identity = (int(entry.name), int(values[19]))
                previous = self.process_previous.get(identity)
                cpu = max(0, (ticks - previous) / self.ticks / elapsed * 100) if previous is not None and elapsed else None
                current[identity] = ticks
                uid = entry.stat().st_uid
                if uid not in self.users:
                    try:
                        self.users[uid] = pwd.getpwuid(uid).pw_name
                    except KeyError:
                        self.users[uid] = str(uid)
                rows.append({"pid": int(entry.name), "name": name, "user": self.users[uid],
                             "state": values[0], "cpu": cpu,
                             "memory": int(values[21]) * self.page_size})
            except (OSError, ValueError, IndexError):
                continue  # Processes can exit while /proc is being read.
        self.process_previous = current
        return sorted(rows, key=lambda row: (row['cpu'] or 0, row['memory']), reverse=True)

    def storage(self):
        rows, seen = [], set()
        virtual = {'proc', 'sysfs', 'devtmpfs', 'devpts', 'tmpfs', 'cgroup', 'cgroup2',
                   'securityfs', 'pstore', 'debugfs', 'tracefs', 'configfs', 'fusectl',
                   'mqueue', 'hugetlbfs', 'autofs', 'binfmt_misc', 'rpc_pipefs', 'nsfs'}
        for line in read('/proc/self/mountinfo').splitlines():
            try:
                left, right = line.split(' - ', 1)
                fields, info = left.split(), right.split()
                mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
                if info[0] in virtual or fields[2] in seen:
                    continue
                seen.add(fields[2])
                usage = shutil.disk_usage(mount)
                rows.append({"mount": mount, "device": info[1], "type": info[0],
                             "total": usage.total, "used": usage.used, "free": usage.free})
            except (OSError, ValueError, IndexError):
                continue
        return sorted(rows, key=lambda row: row['mount'] != '/')

    def networks(self, elapsed):
        rows, current = [], {}
        for line in read('/proc/net/dev').splitlines()[2:]:
            name, data = line.split(':', 1)
            name, values = name.strip(), data.split()
            rx, tx = int(values[0]), int(values[8])
            old = self.net_previous.get(name)
            base = Path('/sys/class/net') / name
            rows.append({"name": name, "state": read(base / 'operstate', 'unknown'),
                         "address": read(base / 'address', '—'),
                         "ip": ', '.join(self.slow['addresses'].get(name, [])) or '—',
                         "rx": rx, "tx": tx,
                         "rx_rate": max(0, (rx - old[0]) / elapsed) if old and elapsed else None,
                         "tx_rate": max(0, (tx - old[1]) / elapsed) if old and elapsed else None,
                         "errors": int(values[2]) + int(values[10])})
            current[name] = (rx, tx)
        self.net_previous = current
        return sorted(rows, key=lambda row: (row['name'] == 'lo', row['name']))

    def sensors(self):
        rows = []
        for chip in sorted(Path('/sys/class/hwmon').glob('hwmon*')):
            name = read(chip / 'name', chip.name)
            for prefix, unit, divisor in [('temp', '°C', 1000), ('fan', 'RPM', 1), ('in', 'V', 1000), ('power', 'W', 1000000)]:
                for sensor in sorted(chip.glob(f'{prefix}*_input')):
                    stem = sensor.name.removesuffix('_input')
                    try:
                        value = float(read(sensor)) / divisor
                    except ValueError:
                        continue
                    limit = read(chip / f'{stem}_crit') or read(chip / f'{stem}_max')
                    try:
                        limit = float(limit) / divisor
                    except ValueError:
                        limit = None
                    rows.append({"id": str(sensor), "chip": name, "name": read(chip / f'{stem}_label', stem),
                                 "kind": prefix, "value": value, "unit": unit, "limit": limit})
        if not any(row['kind'] == 'temp' for row in rows):
            for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
                try:
                    rows.append({"id": str(zone), "chip": 'thermal', "name": read(zone / 'type', zone.name),
                                 "kind": 'temp', "value": float(read(zone / 'temp')) / 1000,
                                 "unit": '°C', "limit": None})
                except ValueError:
                    continue
        return rows

    def disk_io(self, elapsed):
        result, current = [], {}
        for line in read('/proc/diskstats').splitlines():
            fields = line.split()
            if len(fields) < 14:
                continue
            name = fields[2]
            if name.startswith(('loop', 'ram')) or (Path('/sys/class/block') / name / 'partition').exists():
                continue
            reads, writes = int(fields[5]) * 512, int(fields[9]) * 512
            current[name] = (reads, writes)
            old = self.disk_previous.get(name)
            result.append({"name": name,
                           "read": max(0, (reads - old[0]) / elapsed) if old and elapsed else None,
                           "write": max(0, (writes - old[1]) / elapsed) if old and elapsed else None})
        self.disk_previous = current
        return result

    def slow_data(self):
        output = command(['systemctl', 'list-units', '--type=service', '--all', '--no-pager', '--plain', '--no-legend'])
        services = []
        for line in (output or '').splitlines():
            values = line.strip().removeprefix('●').strip().split(None, 4)
            if len(values) == 5:
                services.append(dict(zip(('name', 'load', 'active', 'sub', 'description'), values)))
        applications, seen = [], set()
        data_home = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
        directories = [data_home] + [Path(p) for p in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':') if p]
        for directory in directories:
            for desktop in sorted((directory / 'applications').glob('**/*.desktop')):
                app_id = str(desktop.relative_to(directory / 'applications')).replace('/', '-')
                if app_id in seen:
                    continue
                seen.add(app_id)
                config = configparser.ConfigParser(interpolation=None, strict=False)
                try:
                    config.read(desktop, encoding='utf-8')
                    entry = config['Desktop Entry']
                    if entry.get('Type') != 'Application' or entry.get('Hidden', '').lower() == 'true' or entry.get('NoDisplay', '').lower() == 'true':
                        continue
                    applications.append({"name": entry.get('Name', desktop.stem),
                                         "description": entry.get('Comment', ''),
                                         "exec": entry.get('Exec', ''),
                                         "categories": entry.get('Categories', '').replace(';', ' ').strip()})
                except (configparser.Error, OSError, UnicodeError):
                    continue
        addresses = {}
        ip_output = command(['ip', '-j', 'address', 'show'])
        if ip_output:
            try:
                addresses = {item['ifname']: [a['local'] for a in item.get('addr_info', [])]
                             for item in json.loads(ip_output)}
            except (ValueError, KeyError, TypeError):
                pass
        self.slow = {"services": services, "services_available": output is not None,
                     "applications": sorted(applications, key=lambda row: row['name'].lower()),
                     "addresses": addresses}

    def sample(self):
        if time.monotonic() - self.slow_time > 15:
            self.slow_data()
            self.slow_time = time.monotonic()
        now = time.monotonic()
        elapsed = now - self.last_time if self.last_time is not None else None
        self.last_time = now
        cpu = self.cpu()
        os_release = {}
        for line in read('/etc/os-release').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                os_release[key] = value.strip('"')
        return {"timestamp": time.time(), "host": platform.node(), "kernel": platform.release(),
                "os": os_release.get('PRETTY_NAME', 'Linux'), "arch": platform.machine(),
                "uptime": float(read('/proc/uptime', '0 0').split()[0]),
                "load": os.getloadavg(), "cpu": cpu, "cpu_mhz": self.frequency(), "memory": self.memory(),
                "processes": self.processes(elapsed), "storage": self.storage(),
                "networks": self.networks(elapsed), "sensors": self.sensors(),
                "disk_io": self.disk_io(elapsed), **self.slow}
