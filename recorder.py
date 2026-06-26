"""
recorder.py — coleta métricas do sistema e grava no SQLite periodicamente.
Rode separado do servidor:  python recorder.py [intervalo_segundos]
Default: 60 segundos entre coletas.
"""
import time
import sys
import psutil
from db import init_db, get_conn

INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def collect_disks(conn):
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        conn.execute(
            "INSERT INTO disk_snapshots (device, mountpoint, total_gb, used_gb, free_gb, percent) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                part.device,
                part.mountpoint,
                round(u.total / 1024**3, 3),
                round(u.used / 1024**3, 3),
                round(u.free / 1024**3, 3),
                u.percent,
            ),
        )


def collect_ram(conn):
    m = psutil.virtual_memory()
    conn.execute(
        "INSERT INTO ram_snapshots (total_gb, used_gb, free_gb, percent) VALUES (?, ?, ?, ?)",
        (
            round(m.total / 1024**3, 3),
            round(m.used / 1024**3, 3),
            round(m.available / 1024**3, 3),
            m.percent,
        ),
    )


def collect_cpu(conn):
    pct = psutil.cpu_percent(interval=1)
    conn.execute("INSERT INTO cpu_snapshots (percent) VALUES (?)", (pct,))


def tick():
    with get_conn() as conn:
        collect_disks(conn)
        collect_ram(conn)
        collect_cpu(conn)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] snapshot gravado")


if __name__ == "__main__":
    init_db()
    print(f"Recorder iniciado — intervalo: {INTERVAL}s  |  Ctrl+C para parar")
    while True:
        tick()
        time.sleep(INTERVAL)
