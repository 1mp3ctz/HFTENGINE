#!/bin/bash
# Download Binance USD-M futures daily archives (bookTicker + trades) from data.binance.vision,
# verify each against its .CHECKSUM, resume partial files.
#   tools/fetch_binance_archive.sh BTCUSDT 2024-03
set -euo pipefail
SYM=${1:-BTCUSDT}; MONTH=${2:-2024-03}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE=https://data.binance.vision/data/futures/um/daily
DEST="$ROOT/data/binance/$SYM/$MONTH"
DAYS=$(python3 -c "import calendar,sys;y,m=map(int,'$MONTH'.split('-'));print(calendar.monthrange(y,m)[1])")
for kind in bookTicker trades; do
  mkdir -p "$DEST/$kind"
  for d in $(seq -w 1 "$DAYS"); do
    f="$SYM-$kind-$MONTH-$d.zip"; out="$DEST/$kind/$f"
    if [ -f "$out.ok" ]; then continue; fi
    code=$(curl -s -o /dev/null -w "%{http_code}" -I "$BASE/$kind/$SYM/$f")
    if [ "$code" = "404" ]; then echo "skip $kind $MONTH-$d not in archive"; continue; fi
    curl -sSf -C - --retry 5 -o "$out" "$BASE/$kind/$SYM/$f"
    want=$(curl -sSf "$BASE/$kind/$SYM/$f.CHECKSUM" | awk '{print $1}')
    got=$(shasum -a 256 "$out" | awk '{print $1}')
    if [ "$want" = "$got" ]; then touch "$out.ok"; echo "ok   $kind $MONTH-$d $(du -h "$out" | cut -f1)"
    else echo "BAD  $kind $MONTH-$d checksum mismatch"; rm -f "$out"; fi
  done
done
echo "done $DEST"
