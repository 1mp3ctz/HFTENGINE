"""Convert one day of Binance USD-M futures archives (data.binance.vision bookTicker + trades)
into hftbacktest event files, one .npz per UTC hour.

The archive has no full depth, only best bid/ask (L1) and trades, at millisecond exchange time and
without local receipt time. Each best bid/ask change becomes a pair of plain depth events (old
price level removed, new level set), because the backtest ignores DEPTH_BBO_EVENT. Local time is
exchange time plus a constant feed latency. Every day file starts with a full depth clear.

    python tools/binance_archive.py --day 2024-03-30
    -> data/binance/BTCUSDT/2024-03/npz/2024-03-30/00.npz ... 23.npz
"""
from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

import numpy as np
import polars as pl
from hftbacktest import (
    BUY_EVENT,
    DEPTH_CLEAR_EVENT,
    DEPTH_EVENT,
    EXCH_EVENT,
    LOCAL_EVENT,
    SELL_EVENT,
    TRADE_EVENT,
)
from hftbacktest.types import event_dtype

ROOT = Path(__file__).resolve().parents[1]
HOUR_NS = 3_600_000_000_000
BLOCK = 64 << 20
BOOK_COLS = ["update_id", "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty",
             "transaction_time", "event_time"]
BOTH = EXCH_EVENT | LOCAL_EVENT


def csv_blocks(zpath: Path, columns: list[str]):
    with zipfile.ZipFile(zpath) as z, z.open(z.namelist()[0]) as f:
        header = f.readline()
        if not header.startswith(columns[0].encode()):
            raise ValueError(f"{zpath.name}: unexpected header {header[:80]!r}")
        rest = b""
        while True:
            chunk = f.read(BLOCK)
            if not chunk:
                break
            chunk = rest + chunk
            cut = chunk.rfind(b"\n") + 1
            rest = chunk[cut:]
            if cut:
                yield pl.read_csv(io.BytesIO(chunk[:cut]), has_header=False, new_columns=columns)
        if rest.strip():
            yield pl.read_csv(io.BytesIO(rest), has_header=False, new_columns=columns)


def book_events(df: pl.DataFrame, state: dict) -> np.ndarray:
    ts = df["transaction_time"].to_numpy().astype(np.int64) * 1_000_000
    parts = []
    for side_rank, (key, flag) in enumerate((("bid", BUY_EVENT), ("ask", SELL_EVENT))):
        px = df[f"best_{key}_price"].to_numpy()
        qty = df[f"best_{key}_qty"].to_numpy()
        prev_px = np.concatenate(([state.get(f"{key}_px", np.nan)], px[:-1]))
        prev_qty = np.concatenate(([state.get(f"{key}_qty", np.nan)], qty[:-1]))
        moved = (px != prev_px) & ~np.isnan(prev_px)
        changed = (px != prev_px) | (qty != prev_qty)
        rm = np.zeros(moved.sum(), event_dtype)
        rm["ev"] = BOTH | DEPTH_EVENT | flag
        rm["exch_ts"] = ts[moved]
        rm["px"] = prev_px[moved]
        st = np.zeros(changed.sum(), event_dtype)
        st["ev"] = BOTH | DEPTH_EVENT | flag
        st["exch_ts"] = ts[changed]
        st["px"] = px[changed]
        st["qty"] = qty[changed]
        parts += [(st, np.nonzero(changed)[0], side_rank), (rm, np.nonzero(moved)[0], 2 + side_rank)]
        state[f"{key}_px"], state[f"{key}_qty"] = px[-1], qty[-1]
    out = np.concatenate([a for a, _, _ in parts])
    row = np.concatenate([r for _, r, _ in parts])
    rank = np.concatenate([np.full(len(a), k, np.int8) for a, _, k in parts])
    return out[np.lexsort((rank, row))]


def trade_events(day_zip: Path) -> np.ndarray:
    df = pl.concat(list(csv_blocks(day_zip, ["id", "price", "qty", "quote_qty", "time", "is_buyer_maker"])))
    ev = np.zeros(len(df), event_dtype)
    sell = df["is_buyer_maker"].to_numpy()
    ev["ev"] = BOTH | TRADE_EVENT | np.where(sell, SELL_EVENT, BUY_EVENT)
    ev["exch_ts"] = df["time"].to_numpy().astype(np.int64) * 1_000_000
    ev["px"] = df["price"].to_numpy()
    ev["qty"] = df["qty"].to_numpy()
    return ev[np.argsort(ev["exch_ts"], kind="stable")]


def write_hour(out_dir: Path, hour: int, book: list[np.ndarray], trades: np.ndarray,
               lat_ns: int, clear_ts: int | None) -> int:
    parts = [np.concatenate(book) if book else np.zeros(0, event_dtype)]
    if clear_ts is not None:
        clr = np.zeros(1, event_dtype)
        clr["ev"] = BOTH | DEPTH_CLEAR_EVENT
        clr["exch_ts"] = clear_ts
        parts.insert(0, clr)
    data = np.concatenate(parts + [trades])
    is_book = np.concatenate([np.ones(sum(len(p) for p in parts), bool), np.zeros(len(trades), bool)])
    data = data[np.lexsort((is_book, data["exch_ts"]))]
    data["local_ts"] = data["exch_ts"] + lat_ns
    np.savez_compressed(out_dir / f"{hour:02d}.npz", data=data)
    return len(data)


def convert_day(sym: str, day: str, feed_latency_ms: float) -> Path:
    month = day[:7]
    base = ROOT / "data" / "binance" / sym / month
    book_zip = base / "bookTicker" / f"{sym}-bookTicker-{day}.zip"
    trade_zip = base / "trades" / f"{sym}-trades-{day}.zip"
    for p in (book_zip, trade_zip):
        if not (p.parent / (p.name + ".ok")).exists():
            raise SystemExit(f"missing or unverified: {p}")
    out_dir = base / "npz" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    lat_ns = int(feed_latency_ms * 1_000_000)

    trades = trade_events(trade_zip)
    day_start = (int(trades["exch_ts"][0]) // (24 * HOUR_NS)) * 24 * HOUR_NS
    t_hour = ((trades["exch_ts"] - day_start) // HOUR_NS).astype(np.int64)

    state: dict = {}
    pending: list[np.ndarray] = []
    cur = 0
    first_ts = None
    total = 0

    def flush(h: int) -> None:
        nonlocal pending, total, first_ts
        n = write_hour(out_dir, h, pending, trades[t_hour == h], lat_ns, first_ts)
        print(f"  {day} {h:02d}h {n:>10,} events")
        total += n
        pending, first_ts = [], None

    for df in csv_blocks(book_zip, BOOK_COLS):
        ev = book_events(df, state)
        if first_ts is None and cur == 0 and not pending:
            first_ts = int(ev["exch_ts"][0]) - 1
        hrs = ((ev["exch_ts"] - day_start) // HOUR_NS).astype(np.int64)
        for h in np.unique(hrs):
            while cur < h:
                flush(cur)
                cur += 1
            pending.append(ev[hrs == h])
    while cur < 24:
        flush(cur)
        cur += 1
    print(f"{day}: {total:,} events -> {out_dir}")
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, nargs="+", help="YYYY-MM-DD (one or more)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--feed-latency-ms", type=float, default=5.0)
    a = ap.parse_args()
    for d in a.day:
        convert_day(a.symbol, d, a.feed_latency_ms)


if __name__ == "__main__":
    main()
