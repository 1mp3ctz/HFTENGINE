"""One command from downloaded Binance archive days to a replayable dashboard session.

    python tools/binance_session.py --day 2024-03-30
    python tools/binance_session.py --day 2024-03-15 --hours 13 17 -- --order-qty 0.005

Converts the day if needed (tools/binance_archive.py), runs the runner with settings that fit
best-bid/ask-only data (1 recorded level, quoting at the touch only, constant latency, retail
maker fee) and stages the recording in dashboard/public/sessions/. Extra runner args after `--`
override the defaults.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from binance_archive import convert_day  # noqa: E402

RUNNER = ROOT / "runner" / "target" / "release" / ("hftengine-runner.exe" if sys.platform == "win32" else "hftengine-runner")
PY = Path(sys.executable)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--hours", type=int, nargs=2, default=[0, 24], metavar=("FROM", "TO"))
    ap.add_argument("--latency-ms", type=float, default=5.0, help="feed and order latency (one way)")
    ap.add_argument("--name", default=None)
    ap.add_argument("runner_args", nargs="*")
    a = ap.parse_args()

    npz_dir = ROOT / "data" / "binance" / a.symbol / a.day[:7] / "npz" / a.day
    if len(list(npz_dir.glob("*.npz"))) < 24:
        convert_day(a.symbol, a.day, a.latency_ms)
    files = [str(npz_dir / f"{h:02d}.npz") for h in range(a.hours[0], a.hours[1])]
    name = a.name or f"{a.symbol.lower()}_{a.day}" + ("" if a.hours == [0, 24] else f"_{a.hours[0]:02d}-{a.hours[1]:02d}")
    out = ROOT / "data" / "recordings" / f"{name}.hbr"
    lat_us = str(int(a.latency_ms * 1000))
    cmd = [str(RUNNER), "--data", *files, "--const-latency-us", lat_us, lat_us,
           "--symbol", a.symbol, "--levels", "1", "--grid-num", "1", "--maker-fee", "0.0002",
           "--out", str(out), *a.runner_args]
    subprocess.run(cmd, check=True, cwd=ROOT)

    dst = ROOT / "dashboard" / "public" / "sessions"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, dst / f"{name}.hbr")
    (dst / "default.json").write_text(json.dumps({"session": name}))
    subprocess.run([str(PY), str(ROOT / "tools" / "hbr.py"), str(out)], cwd=ROOT)
    print(f"\nopen -> http://127.0.0.1:5180/?session={name}")


if __name__ == "__main__":
    main()
