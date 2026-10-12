#!/usr/bin/env python3
"""
patch_miner.py - passt tools/miner.py einmalig an, damit er die items_<xpac>.pkl
aus dem eigenen tools-Ordner liest statt von D:/Projects/Current/WoW/WoWData/...
(dem privaten Pfad des Autors).

Aufruf (im tools-Ordner des AutoBar-Repos):
    python patch_miner.py
Legt vorher eine Sicherung miner.py.bak an. Mehrfaches Ausfuehren ist harmlos.
"""
import re
import shutil
import sys
from pathlib import Path

miner = Path("miner.py")
if not miner.is_file():
    sys.exit("miner.py nicht gefunden - bitte im Ordner 'tools' des AutoBar-Repos ausfuehren.")

src = miner.read_text(encoding="utf-8")
pattern = r'file_name = f"D:/Projects/Current/WoW/WoWData/ItemData/items_\{muffin_xpac\}\.pkl"'
replacement = 'file_name = f"items_{muffin_xpac}.pkl"'

if not re.search(pattern, src):
    if replacement in src:
        sys.exit("Schon gepatcht - nichts zu tun.")
    sys.exit("Die erwartete Zeile wurde nicht gefunden (miner.py wurde evtl. vom Autor geaendert). "
             "Suche nach 'WoWData/ItemData' und ersetze den Pfad von Hand.")

shutil.copy(miner, "miner.py.bak")
miner.write_text(re.sub(pattern, replacement, src), encoding="utf-8")
print("OK: miner.py gepatcht (Sicherung: miner.py.bak)")
