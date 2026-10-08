# Replaying Binance's public archive (no recording needed)

`data.binance.vision` publishes free daily files for Binance USD-M futures. Two of them are
enough for a best-bid/ask replay:

| file | content |
| --- | --- |
| `bookTicker` | every change of the best bid / best ask (price and size), millisecond exchange time |
| `trades` | every trade with price, size and aggressor side |

Binance stopped publishing `bookTicker` after 2024-03-30, so March 2024 is the most recent month
with order-book data in the archive. `trades` continues for every month.

## Download

```
tools/fetch_binance_archive.sh BTCUSDT 2024-03
```

Daily zips land in `data/binance/<SYMBOL>/<YYYY-MM>/{bookTicker,trades}/`. Each file is checked
against Binance's SHA-256 `.CHECKSUM` (a `.ok` marker is written next to it); interrupted downloads
resume, verified files are skipped, days missing from the archive are logged and skipped.
BTCUSDT March 2024 is about 8 GB.

## Convert and replay

```
python tools/binance_session.py --day 2024-03-15
python tools/binance_session.py --day 2024-03-15 --hours 13 17 -- --order-qty 0.005
```

The first run converts the day (`tools/binance_archive.py`) into 24 hourly event files under
`data/binance/<SYMBOL>/<YYYY-MM>/npz/<day>/`, then runs the runner and stages the session in
`dashboard/public/sessions/`. A full day of BTCUSDT (about 9 million events) replays in a few
seconds.

## What differs from a collector recording

- **One level per side.** Only the best bid and ask exist, so the book panel shows one level and
  the session defaults to `--levels 1 --grid-num 1` (quote at the touch only). Deeper quotes would
  sit on levels the data cannot see and would be filled as if nobody were ahead of them.
- **Constant latency.** The archive has exchange timestamps only. Local receipt time is exchange
  time plus `--latency-ms` (default 5 ms, near the exchange); the same value is used for order
  entry and response. Use e.g. `--latency-ms 200` for a home connection in Europe.
- **Retail maker fee** (`--maker-fee 0.0002`) is the default here instead of the tutorial rebate.

## Conversion notes

- The backtest does not apply `DEPTH_BBO_EVENT`, so each best bid/ask change is written as plain
  depth events: the new level is set, then the previous level is removed, in the original row
  order (removing first makes the depth scan walk the whole price range; reordering within a
  millisecond leaves stale levels).
- Every day starts with a depth clear, so days can be replayed in any combination.
