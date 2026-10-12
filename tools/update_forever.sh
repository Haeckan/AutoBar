#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY=../.venv/bin/python

git checkout forever-data
$PY -u build_items_forever.py                 # neue Items/Tooltips holen
rm -f wowhead_cache_camelot.pkl wowhead_cache_camelot.json   # sonst alte Listen!
$PY -u miner.py camelot                       # Lua-Listen neu berechnen

cd ..
if [[ -n "$(git status --porcelain camelot)" ]]; then
  git add camelot
  git commit -m "Forever: Item-Listen aktualisiert $(date +%F)"
  git push origin forever-data
else
  echo "Keine Änderungen."
fi
