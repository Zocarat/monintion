from flask import Flask, jsonify, render_template, request
import psutil
import threading
import time
import os
import sys
import stat
import shutil
import glob as globmod
import platform
from datetime import datetime, timedelta
from db import init_db, get_conn

app = Flask(__name__)
init_db()

# ── recorder ─────────────────────────────────────────────────────────────────

RECORD_INTERVAL = 30

def _record_tick():
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO disk_snapshots (device, mountpoint, total_gb, used_gb, free_gb, percent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (part.device, part.mountpoint,
                 round(u.total/1024**3, 3), round(u.used/1024**3, 3),
                 round(u.free/1024**3, 3), u.percent),
            )
    m = psutil.virtual_memory()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ram_snapshots (total_gb, used_gb, free_gb, percent) VALUES (?, ?, ?, ?)",
            (round(m.total/1024**3,3), round(m.used/1024**3,3),
             round(m.available/1024**3,3), m.percent),
        )
        conn.execute("INSERT INTO cpu_snapshots (percent) VALUES (?)",
                     (psutil.cpu_percent(interval=1),))

def _recorder_loop():
    while True:
        try: _record_tick()
        except Exception as e: print(f"[recorder] erro: {e}")
        time.sleep(RECORD_INTERVAL)

threading.Thread(target=_recorder_loop, daemon=True).start()
print(f"[recorder] iniciado — gravando a cada {RECORD_INTERVAL}s")

# ── helpers ───────────────────────────────────────────────────────────────────

def fmt_size(b):
    for unit in ('B','KB','MB','GB','TB'):
        if b < 1024: return round(b, 2), unit
        b /= 1024
    return round(b, 2), 'TB'

def get_disk_data():
    out = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        out.append({
            "device": part.device, "mountpoint": part.mountpoint, "fstype": part.fstype,
            "total_gb": round(u.total/1024**3, 2), "used_gb": round(u.used/1024**3, 2),
            "free_gb": round(u.free/1024**3, 2), "percent": u.percent,
        })
    return out

# ── live endpoints ────────────────────────────────────────────────────────────

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/disks")
def api_disks(): return jsonify(get_disk_data())

@app.route("/api/ram")
def api_ram():
    m = psutil.virtual_memory()
    return jsonify({"total_gb": round(m.total/1024**3,2), "used_gb": round(m.used/1024**3,2),
                    "free_gb": round(m.available/1024**3,2), "percent": m.percent})

@app.route("/api/cpu")
def api_cpu():
    pct = psutil.cpu_percent(interval=0.3, percpu=True)
    return jsonify({"percent": sum(pct)/len(pct), "cores": pct,
                    "count": psutil.cpu_count(), "freq": round(psutil.cpu_freq().current) if psutil.cpu_freq() else 0})

@app.route("/api/system")
def api_system():
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot
    h, rem = divmod(int(uptime.total_seconds()), 3600)
    m2, s = divmod(rem, 60)
    procs = len(psutil.pids())
    net = psutil.net_io_counters()
    return jsonify({
        "uptime": f"{h}h {m2}m",
        "processes": procs,
        "net_sent_mb": round(net.bytes_sent/1024**2, 1),
        "net_recv_mb": round(net.bytes_recv/1024**2, 1),
    })

# ── history endpoints ─────────────────────────────────────────────────────────

@app.route("/api/history/disks")
def history_disks():
    mountpoint = request.args.get("mountpoint")
    limit = min(int(request.args.get("limit", 100)), 1000)
    with get_conn() as conn:
        if mountpoint:
            rows = conn.execute(
                "SELECT ts, device, mountpoint, used_gb, free_gb, percent FROM disk_snapshots "
                "WHERE mountpoint=? ORDER BY ts DESC LIMIT ?", (mountpoint, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, device, mountpoint, used_gb, free_gb, percent FROM disk_snapshots "
                "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/history/ram")
def history_ram():
    limit = min(int(request.args.get("limit", 100)), 1000)
    with get_conn() as conn:
        rows = conn.execute("SELECT ts, used_gb, free_gb, percent FROM ram_snapshots "
                            "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/history/cpu")
def history_cpu():
    limit = min(int(request.args.get("limit", 100)), 1000)
    with get_conn() as conn:
        rows = conn.execute("SELECT ts, percent FROM cpu_snapshots "
                            "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return jsonify([dict(r) for r in rows])

# ── file browser ──────────────────────────────────────────────────────────────

@app.route("/api/files")
def api_files():
    path = request.args.get("path", os.path.splitdrive(os.getcwd())[0] + "\\")
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return jsonify({"error": "Caminho inválido"}), 400
    entries = []
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
                is_dir = stat.S_ISDIR(st.st_mode)
                size_bytes = st.st_size if not is_dir else 0
                val, unit = fmt_size(size_bytes)
                entries.append({
                    "name": name, "type": "dir" if is_dir else "file",
                    "ext": "" if is_dir else os.path.splitext(name)[1].lstrip(".").lower(),
                    "size_bytes": size_bytes, "size_val": val, "size_unit": unit,
                    "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "modified_ts": int(st.st_mtime),
                    "path": full,
                })
            except (PermissionError, OSError):
                continue
    except PermissionError:
        return jsonify({"error": "Sem permissão"}), 403

    drives = [p.mountpoint for p in psutil.disk_partitions()]
    parent = str(os.path.dirname(path)) if path != os.path.splitdrive(path)[0] + "\\" else None
    return jsonify({"path": path, "parent": parent, "drives": drives, "entries": entries})


@app.route("/api/files/largest")
def api_files_largest():
    path = request.args.get("path", "C:\\")
    limit = min(int(request.args.get("limit", 20)), 100)
    results = []
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                full = os.path.join(root, f)
                try:
                    size = os.path.getsize(full)
                    val, unit = fmt_size(size)
                    results.append({"name": f, "path": full, "size_bytes": size,
                                    "size_val": val, "size_unit": unit,
                                    "ext": os.path.splitext(f)[1].lstrip(".").lower()})
                except (PermissionError, OSError):
                    continue
            if len(results) > 5000:
                break
    except PermissionError:
        pass
    results.sort(key=lambda x: x["size_bytes"], reverse=True)
    return jsonify(results[:limit])


@app.route("/api/files/ext-stats")
def api_files_ext_stats():
    path = request.args.get("path", "C:\\")
    stats = {}
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
                if stat.S_ISREG(st.st_mode):
                    ext = os.path.splitext(name)[1].lstrip(".").lower() or "sem ext"
                    stats.setdefault(ext, {"count": 0, "size_bytes": 0})
                    stats[ext]["count"] += 1
                    stats[ext]["size_bytes"] += st.st_size
            except (PermissionError, OSError):
                continue
    except PermissionError:
        pass
    out = []
    for ext, d in stats.items():
        v, u = fmt_size(d["size_bytes"])
        out.append({"ext": ext, "count": d["count"],
                    "size_bytes": d["size_bytes"], "size_val": v, "size_unit": u})
    out.sort(key=lambda x: x["size_bytes"], reverse=True)
    return jsonify(out[:20])


# ── cleaner ───────────────────────────────────────────────────────────────────

IS_WIN = platform.system() == "Windows"

def _dir_size(path):
    total = 0
    try:
        for root, dirs, files in os.walk(path, onerror=lambda e: None):
            for f in files:
                try: total += os.path.getsize(os.path.join(root, f))
                except OSError: pass
    except Exception:
        pass
    return total

def _file_list(path):
    items = []
    try:
        for root, dirs, files in os.walk(path, onerror=lambda e: None):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    items.append({"path": fp, "size": os.path.getsize(fp)})
                except OSError:
                    pass
    except Exception:
        pass
    return items

def _glob_size(patterns):
    total, items = 0, []
    for pat in patterns:
        for fp in globmod.glob(pat, recursive=True):
            try:
                s = os.path.getsize(fp)
                total += s
                items.append({"path": fp, "size": s})
            except OSError:
                pass
    return total, items

def _get_categories():
    home = os.path.expanduser("~")
    cats = []

    if IS_WIN:
        win_temp = os.environ.get("TEMP", os.path.join(home, "AppData", "Local", "Temp"))
        sys_temp = r"C:\Windows\Temp"
        prefetch = r"C:\Windows\Prefetch"
        thumb    = os.path.join(home, "AppData", "Local", "Microsoft", "Windows", "Explorer")
        ie_cache = os.path.join(home, "AppData", "Local", "Microsoft", "Windows", "INetCache")
        edge_cache = os.path.join(home, "AppData", "Local", "Microsoft", "Edge", "User Data", "Default", "Cache")
        chrome_cache = os.path.join(home, "AppData", "Local", "Google", "Chrome", "User Data", "Default", "Cache")
        firefox_cache = os.path.join(home, "AppData", "Local", "Mozilla", "Firefox", "Profiles")

        cats = [
            {"id": "win_temp",     "label": "Arquivos Temporários do Usuário", "icon": "🗑️",  "paths": [win_temp],     "recursive": True},
            {"id": "sys_temp",     "label": "Temp do Sistema (Windows)",        "icon": "⚙️",  "paths": [sys_temp],     "recursive": True},
            {"id": "prefetch",     "label": "Prefetch do Windows",              "icon": "⚡",  "paths": [prefetch],     "recursive": False},
            {"id": "ie_cache",     "label": "Cache do Internet Explorer/Edge",  "icon": "🌐",  "paths": [ie_cache],     "recursive": True},
            {"id": "edge_cache",   "label": "Cache do Microsoft Edge",          "icon": "🌐",  "paths": [edge_cache],   "recursive": True},
            {"id": "chrome_cache", "label": "Cache do Google Chrome",           "icon": "🌐",  "paths": [chrome_cache], "recursive": True},
            {"id": "firefox_cache","label": "Cache do Firefox",                 "icon": "🦊",  "paths": [firefox_cache],"recursive": True},
            {"id": "thumbnails",   "label": "Cache de Miniaturas",              "icon": "🖼️",  "paths": [thumb],        "recursive": True},
        ]
    else:
        apt_cache  = "/var/cache/apt/archives"
        tmp        = "/tmp"
        user_cache = os.path.join(home, ".cache")
        old_logs   = "/var/log"
        pip_cache  = os.path.join(home, ".cache", "pip")
        thumb_lin  = os.path.join(home, ".cache", "thumbnails")

        cats = [
            {"id": "tmp",        "label": "Arquivos /tmp",           "icon": "🗑️", "paths": [tmp],        "recursive": True},
            {"id": "user_cache", "label": "Cache do Usuário (~/.cache)", "icon": "📦","paths": [user_cache], "recursive": True},
            {"id": "pip_cache",  "label": "Cache do pip",            "icon": "🐍", "paths": [pip_cache],  "recursive": True},
            {"id": "apt_cache",  "label": "Cache do apt",            "icon": "📥", "paths": [apt_cache],  "recursive": False},
            {"id": "thumbnails", "label": "Cache de Miniaturas",     "icon": "🖼️", "paths": [thumb_lin],  "recursive": True},
            {"id": "old_logs",   "label": "Logs antigos (/var/log *.gz *.1)", "icon": "📋",
             "paths": [], "patterns": ["/var/log/**/*.gz", "/var/log/**/*.1"], "recursive": True},
        ]

    return cats


@app.route("/api/cleaner/preview")
def cleaner_preview():
    cat_id = request.args.get("id")
    limit  = min(int(request.args.get("limit", 200)), 1000)
    cats   = {c["id"]: c for c in _get_categories()}
    cat    = cats.get(cat_id)
    if not cat:
        return jsonify({"error": "Categoria não encontrada"}), 404

    items = []
    for base in cat.get("paths", []):
        if not os.path.exists(base):
            continue
        for root, dirs, files in os.walk(base, onerror=lambda e: None):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    st  = os.stat(fp)
                    val, unit = fmt_size(st.st_size)
                    items.append({
                        "name":     f,
                        "path":     fp,
                        "size_bytes": st.st_size,
                        "size_val": val,
                        "size_unit": unit,
                        "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "ext":      os.path.splitext(f)[1].lstrip(".").lower(),
                    })
                except OSError:
                    pass
    for pat in cat.get("patterns", []):
        for fp in globmod.glob(pat, recursive=True):
            try:
                st  = os.stat(fp)
                val, unit = fmt_size(st.st_size)
                items.append({
                    "name":     os.path.basename(fp),
                    "path":     fp,
                    "size_bytes": st.st_size,
                    "size_val": val,
                    "size_unit": unit,
                    "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "ext":      os.path.splitext(fp)[1].lstrip(".").lower(),
                })
            except OSError:
                pass

    items.sort(key=lambda x: x["size_bytes"], reverse=True)
    total = sum(i["size_bytes"] for i in items)
    val, unit = fmt_size(total)
    return jsonify({
        "id": cat_id, "label": cat["label"], "icon": cat["icon"],
        "total_bytes": total, "total_val": val, "total_unit": unit,
        "count": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    })


@app.route("/api/cleaner/scan")
def cleaner_scan():
    results = []
    for cat in _get_categories():
        total = 0
        count = 0
        for p in cat.get("paths", []):
            if os.path.exists(p):
                files = _file_list(p)
                total += sum(f["size"] for f in files)
                count += len(files)
        # glob patterns (Linux logs)
        for pat in cat.get("patterns", []):
            for fp in globmod.glob(pat, recursive=True):
                try:
                    total += os.path.getsize(fp)
                    count += 1
                except OSError:
                    pass
        val, unit = fmt_size(total)
        results.append({
            "id":    cat["id"],
            "label": cat["label"],
            "icon":  cat["icon"],
            "size_bytes": total,
            "size_val":   val,
            "size_unit":  unit,
            "count": count,
            "exists": any(os.path.exists(p) for p in cat.get("paths", [])) or bool(cat.get("patterns")),
        })
    return jsonify(results)


@app.route("/api/cleaner/delete", methods=["POST"])
def cleaner_delete():
    data = request.get_json(force=True)
    ids  = set(data.get("ids", []))
    if not ids:
        return jsonify({"error": "Nenhuma categoria selecionada"}), 400

    freed   = 0
    deleted = 0
    errors  = []

    cats_map = {c["id"]: c for c in _get_categories()}
    for cid in ids:
        cat = cats_map.get(cid)
        if not cat:
            continue
        for base in cat.get("paths", []):
            if not os.path.exists(base):
                continue
            for root, dirs, files in os.walk(base, topdown=False, onerror=lambda e: None):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        freed += os.path.getsize(fp)
                        os.remove(fp)
                        deleted += 1
                    except Exception as e:
                        errors.append(str(e))
                for d in dirs:
                    dp = os.path.join(root, d)
                    try:
                        os.rmdir(dp)
                    except Exception:
                        pass
        for pat in cat.get("patterns", []):
            for fp in globmod.glob(pat, recursive=True):
                try:
                    freed += os.path.getsize(fp)
                    os.remove(fp)
                    deleted += 1
                except Exception as e:
                    errors.append(str(e))

    val, unit = fmt_size(freed)
    return jsonify({
        "freed_bytes": freed,
        "freed_val":   val,
        "freed_unit":  unit,
        "deleted":     deleted,
        "errors":      errors[:10],
    })


# ── network ──────────────────────────────────────────────────────────────────

@app.route("/api/network")
def api_network():
    import socket
    af_names = {2: "IPv4", 10: "IPv6", 17: "MAC", 23: "IPv4", -1: "Outro"}
    try:
        af_names[socket.AF_INET]  = "IPv4"
        af_names[socket.AF_INET6] = "IPv6"
    except Exception:
        pass

    addrs_map = psutil.net_if_addrs()
    stats_map = psutil.net_if_stats()
    io_map    = psutil.net_io_counters(pernic=True)

    interfaces = []
    for name, addrs in addrs_map.items():
        st  = stats_map.get(name)
        io  = io_map.get(name)
        addr_list = []
        for a in addrs:
            fam = af_names.get(a.family, str(a.family))
            if fam == "MAC" or (a.address and not a.address.startswith("%")):
                addr_list.append({"family": fam, "addr": a.address, "netmask": a.netmask or ""})
        interfaces.append({
            "name":        name,
            "is_up":       st.isup if st else False,
            "speed_mbps":  st.speed if st else 0,
            "mtu":         st.mtu   if st else 0,
            "addrs":       addr_list,
            "bytes_sent":  io.bytes_sent  if io else 0,
            "bytes_recv":  io.bytes_recv  if io else 0,
            "packets_sent":io.packets_sent if io else 0,
            "packets_recv":io.packets_recv if io else 0,
            "errin":       io.errin   if io else 0,
            "errout":      io.errout  if io else 0,
            "dropin":      io.dropin  if io else 0,
            "dropout":     io.dropout if io else 0,
        })
    interfaces.sort(key=lambda x: (not x["is_up"], x["name"]))

    # connections — ESTABLISHED only, limit 200
    conns = []
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status not in ("ESTABLISHED", "LISTEN", "TIME_WAIT", "CLOSE_WAIT"):
                continue
            laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
            raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
            conns.append({
                "type":   "TCP" if c.type == 1 else "UDP",
                "laddr":  laddr,
                "raddr":  raddr,
                "status": c.status or "",
                "pid":    c.pid or 0,
            })
            if len(conns) >= 200:
                break
    except Exception:
        pass

    total = psutil.net_io_counters()
    return jsonify({
        "interfaces": interfaces,
        "connections": conns,
        "total": {
            "bytes_sent": total.bytes_sent,
            "bytes_recv": total.bytes_recv,
        },
        "ts": time.time(),
    })


# ── system info ──────────────────────────────────────────────────────────────

@app.route("/api/sysinfo")
def api_sysinfo():
    import platform, socket as _sock

    uname = platform.uname()
    boot  = datetime.fromtimestamp(psutil.boot_time())
    up    = datetime.now() - boot

    # CPU
    cpu_freq  = psutil.cpu_freq()
    cpu_times = psutil.cpu_times_percent(interval=0.3)

    # RAM
    vm  = psutil.virtual_memory()
    swp = psutil.swap_memory()

    # disks
    disks = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
            disks.append({
                "device": p.device, "mountpoint": p.mountpoint,
                "fstype": p.fstype, "opts": p.opts,
                "total_gb": round(u.total/1024**3,2),
                "used_gb":  round(u.used/1024**3,2),
                "free_gb":  round(u.free/1024**3,2),
                "percent":  u.percent,
            })
        except PermissionError:
            continue

    # network interfaces (only primary IPs)
    ifaces = []
    for name, addrs in psutil.net_if_addrs().items():
        st = psutil.net_if_stats().get(name)
        for a in addrs:
            if a.family == 2:  # AF_INET IPv4
                ifaces.append({"name": name, "ip": a.address,
                                "netmask": a.netmask or "",
                                "is_up": st.isup if st else False})

    # users
    users = []
    try:
        for u in psutil.users():
            users.append({"name": u.name, "terminal": u.terminal or "",
                          "host": u.host or "", "started": datetime.fromtimestamp(u.started).strftime("%Y-%m-%d %H:%M")})
    except Exception:
        pass

    # temperature (Linux/RPi mainly)
    temps = {}
    try:
        for name, entries in psutil.sensors_temperatures().items():
            temps[name] = [{"label": e.label or name, "current": e.current,
                            "high": e.high, "critical": e.critical} for e in entries]
    except (AttributeError, Exception):
        pass

    # battery
    battery = None
    try:
        b = psutil.sensors_battery()
        if b:
            battery = {"percent": b.percent, "plugged": b.power_plugged,
                       "secs_left": b.secsleft if b.secsleft != -1 else None}
    except (AttributeError, Exception):
        pass

    # Python runtime info
    py = sys.version.split()[0]

    h, rem = divmod(int(up.total_seconds()), 3600)
    m2, s  = divmod(rem, 60)

    return jsonify({
        "os": {
            "system":   uname.system,
            "node":     uname.node,
            "release":  uname.release,
            "version":  uname.version,
            "machine":  uname.machine,
            "processor":uname.processor or platform.processor(),
        },
        "hostname": _sock.gethostname(),
        "python":   py,
        "platform": platform.platform(),
        "boot_time":    boot.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime":       f"{h}h {m2}m {s}s",
        "uptime_secs":  int(up.total_seconds()),
        "cpu": {
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores":  psutil.cpu_count(logical=True),
            "freq_current":   round(cpu_freq.current) if cpu_freq else 0,
            "freq_min":       round(cpu_freq.min)     if cpu_freq else 0,
            "freq_max":       round(cpu_freq.max)     if cpu_freq else 0,
            "percent":        psutil.cpu_percent(interval=0.2),
            "user_pct":       cpu_times.user,
            "system_pct":     cpu_times.system,
            "idle_pct":       cpu_times.idle,
            "ctx_switches":   psutil.cpu_stats().ctx_switches,
            "interrupts":     psutil.cpu_stats().interrupts,
        },
        "ram": {
            "total_gb":     round(vm.total/1024**3, 2),
            "available_gb": round(vm.available/1024**3, 2),
            "used_gb":      round(vm.used/1024**3, 2),
            "percent":      vm.percent,
            "buffers_gb":   round(getattr(vm,'buffers',0)/1024**3, 2),
            "cached_gb":    round(getattr(vm,'cached',0)/1024**3, 2),
        },
        "swap": {
            "total_gb": round(swp.total/1024**3, 2),
            "used_gb":  round(swp.used/1024**3, 2),
            "percent":  swp.percent,
        },
        "disks":    disks,
        "ifaces":   ifaces,
        "users":    users,
        "temps":    temps,
        "battery":  battery,
        "process_count": len(psutil.pids()),
    })


# ── port scanner ─────────────────────────────────────────────────────────────

import socket
import concurrent.futures

_COMMON_SERVICES = {
    21:'FTP',22:'SSH',23:'Telnet',25:'SMTP',53:'DNS',80:'HTTP',
    110:'POP3',143:'IMAP',443:'HTTPS',445:'SMB',3306:'MySQL',
    3389:'RDP',5432:'PostgreSQL',5900:'VNC',6379:'Redis',
    8080:'HTTP-Alt',8443:'HTTPS-Alt',8888:'Jupyter',27017:'MongoDB',
    5050:'MONINTION',1883:'MQTT',5672:'AMQP',9200:'Elasticsearch',
}

def _probe_port(host, port, timeout):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return port, True
    except Exception:
        return port, False

@app.route("/api/network/portscan")
def api_portscan():
    host    = request.args.get("host","127.0.0.1").strip()
    p_from  = max(1,   min(int(request.args.get("from", 1)),   65535))
    p_to    = max(1,   min(int(request.args.get("to",   1024)), 65535))
    timeout = max(0.1, min(float(request.args.get("timeout", 0.5)), 3.0))
    if p_to < p_from: p_from, p_to = p_to, p_from
    if p_to - p_from > 9999:
        return jsonify({"error": "Máximo de 10 000 portas por varredura"}), 400
    if not _is_private_host(host) and not os.environ.get("MONINTION_ALLOW_EXTERNAL_SCAN"):
        return jsonify({"error": f"Host '{host}' não é privado. Apenas IPs RFC 1918 e Tailscale (100.64/10) são permitidos. Para habilitar hosts externos, defina MONINTION_ALLOW_EXTERNAL_SCAN=1."}), 403

    ports   = list(range(p_from, p_to + 1))
    open_ports = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=200) as ex:
        futures = {ex.submit(_probe_port, host, p, timeout): p for p in ports}
        for fut in concurrent.futures.as_completed(futures):
            port, is_open = fut.result()
            if is_open:
                open_ports.append({
                    "port":    port,
                    "service": _COMMON_SERVICES.get(port, ""),
                })

    open_ports.sort(key=lambda x: x["port"])
    return jsonify({
        "host":   host,
        "from":   p_from,
        "to":     p_to,
        "scanned": len(ports),
        "open":   open_ports,
    })


# ── apps ─────────────────────────────────────────────────────────────────────

def _read_win_uninstall_key(hive, subkey, seen, apps):
    import winreg
    try:
        with winreg.OpenKey(hive, subkey) as root:
            count = winreg.QueryInfoKey(root)[0]
            for i in range(count):
                try:
                    name = winreg.EnumKey(root, i)
                    with winreg.OpenKey(root, name) as k:
                        def rv(field, default=None):
                            try: return winreg.QueryValueEx(k, field)[0]
                            except: return default
                        display = rv("DisplayName")
                        if not display:
                            continue
                        key_id = display.strip().lower()
                        if key_id in seen:
                            continue
                        seen.add(key_id)
                        apps.append({
                            "name":         display.strip(),
                            "publisher":    rv("Publisher", ""),
                            "version":      rv("DisplayVersion", ""),
                            "install_date": rv("InstallDate", ""),
                            "size_kb":      rv("EstimatedSize", 0) or 0,
                            "install_loc":  rv("InstallLocation", "") or "",
                        })
                except Exception:
                    continue
    except Exception:
        pass

@app.route("/api/apps")
def api_apps():
    running_names = {}
    try:
        for proc in psutil.process_iter(["name", "memory_info", "exe"]):
            try:
                pname = (proc.info["name"] or "").lower()
                mem   = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
                running_names[pname] = running_names.get(pname, 0) + mem
            except Exception:
                pass
    except Exception:
        pass

    apps = []
    if IS_WIN:
        import winreg
        seen = set()
        _read_win_uninstall_key(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", seen, apps)
        _read_win_uninstall_key(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", seen, apps)
        _read_win_uninstall_key(winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", seen, apps)
    else:
        import subprocess
        try:
            r = subprocess.run(["dpkg", "-l"], capture_output=True, text=True, timeout=10)
            seen = set()
            for line in r.stdout.splitlines():
                if not line.startswith("ii"):
                    continue
                parts = line.split(None, 4)
                if len(parts) < 3 or parts[1] in seen:
                    continue
                seen.add(parts[1])
                apps.append({"name": parts[1], "publisher": "", "version": parts[2],
                             "install_date": "", "size_kb": 0, "install_loc": ""})
        except Exception:
            pass

    out = []
    for a in apps:
        # last used: mtime of any exe in install_loc
        last_used = ""
        loc = a.get("install_loc", "")
        if loc and os.path.isdir(loc):
            try:
                mtimes = []
                for f in os.listdir(loc):
                    if f.lower().endswith(".exe"):
                        try:
                            mtimes.append(os.path.getmtime(os.path.join(loc, f)))
                        except OSError:
                            pass
                if mtimes:
                    last_used = datetime.fromtimestamp(max(mtimes)).strftime("%Y-%m-%d")
            except Exception:
                pass

        # running check — match process exe name against app name (4+ char overlap required)
        mem_mb = 0.0
        running = False
        name_lower = a["name"].lower().replace(" ", "")
        for pname, pmem in running_names.items():
            base = pname.replace(".exe", "")
            if len(base) >= 4 and base in name_lower:
                running = True
                mem_mb = round(pmem / 1024**2, 1)
                break

        size_mb = round(a["size_kb"] / 1024, 1) if a["size_kb"] else 0
        out.append({
            "name":         a["name"],
            "publisher":    a["publisher"],
            "version":      a["version"],
            "install_date": a["install_date"],
            "size_mb":      size_mb,
            "last_used":    last_used,
            "running":      running,
            "mem_mb":       mem_mb,
        })

    out.sort(key=lambda x: x["name"].lower())
    return jsonify(out)


# ── remote SSH ───────────────────────────────────────────────────────────────

import uuid
import ipaddress

_ssh_sessions    = {}  # sid -> {client, host, port, user, connected_at}
_KNOWN_HOSTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monintion_known_hosts")

# Private + CGNAT ranges allowed for port scanner (RFC 1918 + Tailscale 100.64/10)
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / Tailscale
    ipaddress.ip_network("169.254.0.0/16"),
]

def _is_private_host(host):
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(host))
        return any(addr in net for net in _PRIVATE_NETS)
    except Exception:
        return False

def _get_paramiko():
    try:
        import paramiko as _pm
        return _pm
    except ImportError:
        return None

def _sess_exec(client, cmd, timeout=30):
    """Run command, return (stdout, stderr, exit_code)."""
    _, out, err = client.exec_command(cmd, timeout=timeout)
    return (out.read().decode(errors="replace"),
            err.read().decode(errors="replace"),
            out.channel.recv_exit_status())

@app.route("/api/remote/sessions")
def remote_sessions():
    return jsonify([
        {"id": k, "host": v["host"], "port": v["port"],
         "user": v["user"], "connected_at": v["connected_at"]}
        for k, v in _ssh_sessions.items()
    ])

@app.route("/api/remote/connect", methods=["POST"])
def remote_connect():
    paramiko = _get_paramiko()
    if not paramiko:
        return jsonify({"ok": False, "error": "paramiko não instalado neste host. Execute: pip install paramiko"}), 500

    d    = request.get_json(force=True)
    host = d.get("host","").strip()
    port = int(d.get("port", 22))
    user = d.get("user","").strip()
    pwd  = d.get("password","")
    key  = d.get("key_path","").strip()
    if not host or not user:
        return jsonify({"ok": False, "error": "Host e usuário são obrigatórios"}), 400

    # TOFU: trust on first use — saves key locally, rejects if key changes later
    class _TOFUPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            client.get_host_keys().add(hostname, key.get_name(), key)
            try:
                client.get_host_keys().save(_KNOWN_HOSTS_FILE)
            except Exception:
                pass

    client = paramiko.SSHClient()
    try:
        client.load_host_keys(_KNOWN_HOSTS_FILE)
    except FileNotFoundError:
        pass
    client.set_missing_host_key_policy(_TOFUPolicy())
    try:
        if key:
            client.connect(host, port=port, username=user, key_filename=os.path.expanduser(key), timeout=10)
        else:
            client.connect(host, port=port, username=user, password=pwd, timeout=10)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    sid = str(uuid.uuid4())[:8]
    _ssh_sessions[sid] = {
        "client": client, "host": host, "port": port,
        "user": user, "connected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    # quick uname
    uname, _, _ = _sess_exec(client, "uname -srm", timeout=5)
    return jsonify({"ok": True, "session_id": sid, "uname": uname.strip()})

@app.route("/api/remote/disconnect", methods=["POST"])
def remote_disconnect():
    d   = request.get_json(force=True)
    sid = d.get("session_id","")
    s   = _ssh_sessions.pop(sid, None)
    if s:
        try: s["client"].close()
        except: pass
    return jsonify({"ok": True})

@app.route("/api/remote/exec", methods=["POST"])
def remote_exec():
    d   = request.get_json(force=True)
    sid = d.get("session_id","")
    cmd = d.get("command","").strip()
    s   = _ssh_sessions.get(sid)
    if not s:
        return jsonify({"ok": False, "error": "Sessão expirada ou não encontrada"}), 404
    if not cmd:
        return jsonify({"ok": False, "error": "Comando vazio"}), 400
    try:
        out, err, rc = _sess_exec(s["client"], cmd, timeout=60)
        return jsonify({"ok": True, "stdout": out, "stderr": err, "rc": rc})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/remote/monintion-status", methods=["POST"])
def remote_monintion_status():
    d   = request.get_json(force=True)
    sid = d.get("session_id","")
    s   = _ssh_sessions.get(sid)
    if not s:
        return jsonify({"ok": False}), 404
    try:
        out, _, _ = _sess_exec(s["client"], "pgrep -fa 'python.*server.py' 2>/dev/null; echo EXIT", timeout=8)
        running = "server.py" in out
        port_out, _, _ = _sess_exec(s["client"], "ss -tlnp 2>/dev/null | grep ':5050' | head -1 || true", timeout=5)
        return jsonify({"ok": True, "running": running, "port_open": bool(port_out.strip())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/remote/deploy", methods=["POST"])
def remote_deploy():
    d   = request.get_json(force=True)
    sid = d.get("session_id","")
    s   = _ssh_sessions.get(sid)
    if not s:
        return jsonify({"ok": False, "error": "Sessão não encontrada"}), 404

    client = s["client"]
    log    = []

    def run(label, cmd, timeout=90, allow_fail=False):
        log.append(f"[…] {label}")
        out, err, rc = _sess_exec(client, cmd, timeout=timeout)
        if out.strip(): log.append(out.strip()[:500])
        if err.strip(): log.append("[stderr] " + err.strip()[:300])
        if rc not in (0, None) and not allow_fail:
            raise RuntimeError(f"{label} falhou (rc={rc})")
        return out, err, rc

    try:
        home, _, _ = _sess_exec(client, "echo $HOME", timeout=5)
        home = home.strip() or "/root"
        deploy_dir = f"{home}/monintion"
        venv_dir   = f"{deploy_dir}/venv"
        log.append(f"[info] Diretório: {deploy_dir}")

        run("Verificando Python 3…", "python3 --version")

        # stop previous instance
        _sess_exec(client, "pkill -f 'python.*server.py' 2>/dev/null; true", timeout=5)

        # create venv (avoids PEP 668 externally-managed-env block)
        run("Criando virtualenv…",
            f"python3 -m venv {venv_dir} --system-site-packages 2>&1 || python3 -m venv {venv_dir}")

        run("Instalando Flask e psutil no venv…",
            f"{venv_dir}/bin/pip install flask psutil --quiet 2>&1 | tail -5")

        # mkdir + sftp upload
        _sess_exec(client, f"mkdir -p {deploy_dir}/templates", timeout=5)
        log.append("[…] Enviando arquivos via SFTP…")
        sftp = client.open_sftp()
        here = os.path.dirname(os.path.abspath(__file__))
        for src, dst in [
            (os.path.join(here, "server.py"),               f"{deploy_dir}/server.py"),
            (os.path.join(here, "db.py"),                   f"{deploy_dir}/db.py"),
            (os.path.join(here, "requirements.txt"),        f"{deploy_dir}/requirements.txt"),
            (os.path.join(here, "templates","index.html"),  f"{deploy_dir}/templates/index.html"),
        ]:
            if os.path.exists(src):
                sftp.put(src, dst)
                log.append(f"[upload] {os.path.basename(src)} ✓")
        sftp.close()

        # start with venv python — fire-and-forget, do NOT read output (avoids channel block)
        log.append("[…] Iniciando servidor remoto…")
        start_cmd = (
            f"cd {deploy_dir} && "
            f"nohup {venv_dir}/bin/python server.py > monintion.log 2>&1 & "
            f"echo LAUNCHED"
        )
        client.exec_command(start_cmd)   # intentionally not reading stdout/stderr

        time.sleep(4)

        chk, _, _ = _sess_exec(client,
            "pgrep -fa 'python.*server.py' 2>/dev/null | head -1 || echo NOT_RUNNING",
            timeout=8)
        if "NOT_RUNNING" in chk:
            tail, _, _ = _sess_exec(client,
                f"tail -20 {deploy_dir}/monintion.log 2>/dev/null || echo sem log",
                timeout=8)
            log.append("[warn] Processo não detectado. Log:\n" + tail)
            return jsonify({"ok": False, "log": "\n".join(log), "error": "Servidor não iniciou"})

        log.append("[ok] MONINTION rodando na porta 5050 ✓")
        return jsonify({"ok": True, "log": "\n".join(log), "host": s["host"]})

    except Exception as e:
        log.append(f"[erro] {e}")
        return jsonify({"ok": False, "log": "\n".join(log), "error": str(e)})


# ── GNS3 integration ─────────────────────────────────────────────────────────

import urllib.request as _urlreq
import collections

GNS3_URL  = os.environ.get("GNS3_URL",  "http://localhost:3080/v2")
GNS3_AUTH = os.environ.get("GNS3_AUTH", "")   # "user:pass" se autenticação ativa

def _gns3(path, timeout=5):
    import json as _json, base64 as _b64
    req = _urlreq.Request(GNS3_URL + path)
    if GNS3_AUTH:
        req.add_header("Authorization", "Basic " + _b64.b64encode(GNS3_AUTH.encode()).decode())
    with _urlreq.urlopen(req, timeout=timeout) as r:
        return _json.loads(r.read().decode())

def _node_role(name):
    n = name.lower()
    if "kali" in n:                                                               return "attacker"
    if any(x in n for x in ("mikrotik","openwrt","cisco","routeros","vyos","pfsense","juniper")): return "router"
    if "switch" in n:                                                             return "switch"
    if any(x in n for x in ("vpc","pc","host","server","debian","ubuntu","win")): return "victim"
    if any(x in n for x in ("nat","internet","cloud","wan")):                    return "internet"
    return "unknown"

@app.route("/api/gns3/projects")
def api_gns3_projects():
    try:
        return jsonify(_gns3("/projects"))
    except Exception as e:
        return jsonify({"error": str(e), "hint": f"GNS3 rodando em {GNS3_URL}?"}), 503

@app.route("/api/gns3/topology")
def api_gns3_topology():
    pid = request.args.get("project_id", "")
    if not pid:
        return jsonify({"error": "project_id obrigatório"}), 400
    try:
        nodes = _gns3(f"/projects/{pid}/nodes")
        links = _gns3(f"/projects/{pid}/links")
        for n in nodes:
            n["_role"] = _node_role(n.get("name", ""))
        return jsonify({"nodes": nodes, "links": links})
    except Exception as e:
        return jsonify({"error": str(e)}), 503


# ── Threat simulation engine ──────────────────────────────────────────────────

import random as _rand

class _ThreatEngine:
    def __init__(self, buf=500):
        self._lock   = threading.Lock()
        self._buf    = collections.deque(maxlen=buf)
        self._c      = {"threats": 0, "blocked": 0, "vectors": {},
                        "per_min": collections.deque([0]*60, maxlen=60),
                        "_tick": 0, "_min_ts": time.time()}
        self._nodes  = []
        self._status = {}   # name → healthy / scanning / compromised

    def set_nodes(self, nodes):
        with self._lock:
            self._nodes = [{"name": n.get("name","?"), "role": n.get("_role","unknown")} for n in nodes]
            self._status = {n["name"]: self._status.get(n["name"],"healthy") for n in self._nodes}

    def _pick(self, role, fallback):
        pool = [n["name"] for n in self._nodes if n["role"] == role]
        return _rand.choice(pool) if pool else fallback

    def generate(self):
        atk   = self._pick("attacker", "Kali")
        rtr   = self._pick("router",   "MikroTik")
        vic   = self._pick("victim",   "VPC-A")
        tgts  = [n["name"] for n in self._nodes if n["role"] in ("router","switch","victim")] or [rtr, vic]
        tgt   = _rand.choice(tgts)
        iface = _rand.choice(["ether1","ether2","ether3","eth0","e0","vlan10","vlan20"])
        port  = _rand.choice([22,23,80,443,8291,8728,2000,3389])

        T = [
            ("CRIT", f"Brute-force SSH em {rtr}:{port} ({_rand.randint(3,20)} tent/s)",        "Brute-force SSH",      True),
            ("ALTO", f"Port scan TCP/SYN → 192.168.{_rand.randint(1,5)}.0/24",                 "Port scan (Nmap)",     True),
            ("INFO", f"Bloqueado: pacote ARP forjado em {iface}",                               "ARP spoofing",         True),
            ("CRIT", f"Tentativa de login admin em {rtr} WebFig",                               "Web login (RouterOS)", True),
            ("ALTO", f"DNS exfiltration suspeita (entropia {round(_rand.uniform(3.5,5.9),2)})", "DNS suspeito",         True),
            ("INFO", f"Regra IDS-{_rand.randint(1000,9999)} atualizada • snort.local",         None,                   False),
            ("MÉD",  f"Conexão saindo p/ host não-listado :{port}",                             "C2 suspeito",          True),
            ("CRIT", f"Privilege escalation detectada • {vic}",                                 "Escalação",            True),
            ("INFO", f"Snapshot do tráfego salvo • {_rand.randint(800,9999)} pkts",            None,                   False),
            ("ALTO", f"Fingerprint Nmap • origem {atk} ({iface})",                              "Port scan (Nmap)",     True),
            ("MÉD",  f"ICMP flood mitigado em {iface} (>{_rand.randint(150,999)} pps)",        "ICMP flood",           True),
            ("INFO", "Baseline de tráfego recalculada",                                         None,                   False),
            ("ALTO", f"ARP spoofing detectado: {atk} → {tgt}",                                 "ARP spoofing",         True),
            ("CRIT", f"Exploit tentativa em {rtr} (CVE simulado)",                              "Exploração",           True),
            ("MÉD",  f"Conexão suspeita {atk} → {vic}:{port}",                                "C2 suspeito",          True),
        ]
        pool = [t for t in T if t[0] != "INFO"] if _rand.random() > 0.3 else T
        sev, msg, vec, is_atk = _rand.choice(pool)
        blocked = is_atk and _rand.random() < 0.87

        evt = {"ts": datetime.now().strftime("%H:%M:%S"), "sev": sev, "msg": msg,
               "vector": vec, "blocked": blocked,
               "attacker": atk if is_atk else None, "target": tgt if is_atk else None}

        with self._lock:
            self._buf.appendleft(evt)
            c = self._c
            c["threats"] += 1
            c["blocked"] += int(blocked)
            if vec: c["vectors"][vec] = c["vectors"].get(vec, 0) + 1
            c["_tick"] += 1
            if time.time() - c["_min_ts"] >= 60:
                c["per_min"].append(c["_tick"])
                c["_tick"] = 0
                c["_min_ts"] = time.time()
            if is_atk and tgt in self._status:
                cur = self._status[tgt]
                if cur == "healthy"   and _rand.random() < 0.25: self._status[tgt] = "scanning"
                elif cur == "scanning" and sev == "CRIT" and _rand.random() < 0.15: self._status[tgt] = "compromised"
        return evt

    def events(self, limit=50):
        with self._lock: return list(self._buf)[:limit]

    def summary(self):
        with self._lock:
            c = self._c
            compromised = [k for k,v in self._status.items() if v == "compromised"]
            scanning    = [k for k,v in self._status.items() if v == "scanning"]
            block_pct   = round(c["blocked"] / max(c["threats"], 1) * 100, 1)
            level = "BAIXO"
            if c["threats"] > 10: level = "MÉDIO"
            if c["threats"] > 30: level = "ALTO"
            if compromised:       level = "CRÍTICO"
            vecs = sorted(c["vectors"].items(), key=lambda x: -x[1])
            return {
                "threats": c["threats"], "blocked": c["blocked"],
                "block_pct": block_pct,  "level": level,
                "compromised": compromised, "scanning": scanning,
                "vectors": [{"name": k, "count": v} for k, v in vecs[:6]],
                "per_min": list(c["per_min"]),
                "node_status": dict(self._status),
                "origin": [{"label":"Kali (atacante)","pct":62},{"label":"Router mgmt","pct":18},
                           {"label":"NAT externo","pct":12},{"label":"Outros","pct":8}],
            }

    def reset(self):
        with self._lock:
            self._buf.clear()
            self._c = {"threats":0,"blocked":0,"vectors":{},
                       "per_min":collections.deque([0]*60,maxlen=60),
                       "_tick":0,"_min_ts":time.time()}
            for k in self._status: self._status[k] = "healthy"

_engine = _ThreatEngine()

def _threat_loop():
    while True:
        time.sleep(_rand.uniform(2.0, 5.0))
        try: _engine.generate()
        except Exception as e: print(f"[threats] {e}")

threading.Thread(target=_threat_loop, daemon=True).start()

@app.route("/api/threats/events")
def api_threat_events():
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(_engine.events(limit))

@app.route("/api/threats/summary")
def api_threat_summary():
    return jsonify(_engine.summary())

@app.route("/api/threats/nodes", methods=["POST"])
def api_threat_nodes():
    nodes = request.get_json(force=True) or []
    _engine.set_nodes(nodes)
    return jsonify({"ok": True, "count": len(nodes)})

@app.route("/api/threats/reset", methods=["POST"])
def api_threat_reset():
    _engine.reset()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
