#!/usr/bin/env python3
"""
build_items_forever.py
======================
Erzeugt die Datei  items_forever.pkl  fuer den AutoBar-Miner (tools/miner.py),
direkt aus der Wowhead-Forever-Datenbank. Der Autor des Addons hat diese Datei
privat auf seinem Rechner; dieses Skript ersetzt sie.

Format der Datei (so liest miner.py sie):
    { item_id (int): {"item_name": str, "item_type": str, "tooltip_single": str} }

Ablauf:
  1. Item-Listen von Wowhead holen (Consumables, Quest, Misc ...).
  2. Fuer jede Item-ID, die noch nicht in der pkl steht, den Tooltip holen.
  3. Alles speichern. Beim naechsten Lauf werden nur NEUE Items nachgeladen.

Aufruf:
    python build_items_forever.py              # normaler Update-Lauf
    python build_items_forever.py --probe 2070 # nur testen: ein Tooltip, nichts speichern
    python build_items_forever.py --refresh    # alle Tooltips neu laden
"""
import argparse
import html
import pickle
import re
import sys
import time
from pathlib import Path

import json

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- Einstellungen
WOWHEAD_ROOT = "https://www.wowhead.com/forever/"

# Seiten, deren Item-Listen eingelesen werden. Wowhead zeigt pro Seite max. 1000
# Items. Wird die Grenze erreicht, warnt das Skript -> dann die Seite in
# Unterkategorien aufteilen und hier eintragen.
LIST_PAGES = [
    "items/consumables/food-and-drinks",
    "items/consumables/potions",
    "items/consumables/elixirs",
    "items/consumables/flasks",
    "items/consumables/bandages",
    "items/consumables/other",
    "items/consumables",
    "items/quest",
    "items/miscellaneous",
    "items/trade-goods",
]

# Wo der Tooltip pro Item geholt wird. {id} wird ersetzt.
# ACHTUNG: Diese URL ist eine Annahme (bei Classic funktioniert das Muster
# nether.wowhead.com/classic/tooltip/item/<id>). Mit  --probe 2070  pruefen!
TOOLTIP_URL = "https://nether.wowhead.com/forever/tooltip/item/{id}"

# Wowhead "classs"-Nummer -> Name, wie ihn set_camelot.xml erwartet
ITEM_CLASS = {
    0: "Consumable", 1: "Container", 2: "Weapon", 3: "Gem", 4: "Armor",
    5: "Reagent", 6: "Projectile", 7: "Trade Goods", 9: "Recipe",
    11: "Quiver", 12: "Quest", 13: "Key", 15: "Miscellaneous",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0"}
TIMEOUT = 60


# ---------------------------------------------------------------- Hilfsfunktionen
def get(url, retries=3, delay=2.0):
    """HTTP-GET mit ein paar Wiederholungen."""
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def parse_listview(page_text):
    """Holt 'var listviewitems = [...]' aus einer Wowhead-Seite (wie miner.py)."""
    soup = BeautifulSoup(page_text, "html.parser")
    m = re.search(r"var listviewitems\s*=\s*(\[.*\])", soup.prettify())
    if not m:
        return None
    raw = m.group(1)
    try:
        return json.loads(raw)
    except ValueError:
        import demjson3  # toleranter JS-Parser (braucht auch miner.py)
        return demjson3.decode(raw)


def clean_tooltip(raw_html):
    """HTML-Tooltip -> einzeiliger Klartext (so matcht miner.py seine Regex)."""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_tooltip(item_id, url_template):
    """Liefert (name, tooltip_text) fuer eine Item-ID."""
    data = get(url_template.format(id=item_id)).json()
    return data.get("name", ""), clean_tooltip(data.get("tooltip", ""))


def load_pkl(path):
    if path.is_file():
        with open(path, "rb") as f:
            return pickle.load(f)
    return {}


def save_pkl(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(data, f)
    tmp.replace(path)


# ---------------------------------------------------------------- Hauptprogramm
def main():
    ap = argparse.ArgumentParser(description="items_forever.pkl aus Wowhead Forever erzeugen")
    ap.add_argument("--out", default="items_forever.pkl", help="Zieldatei (Standard: ./items_forever.pkl)")
    ap.add_argument("--delay", type=float, default=0.6, help="Pause zwischen Anfragen in Sekunden (Standard 0.6)")
    ap.add_argument("--refresh", action="store_true", help="alle Tooltips neu laden, nicht nur neue Items")
    ap.add_argument("--probe", type=int, metavar="ITEM_ID", help="nur einen Tooltip testen und beenden")
    ap.add_argument("--tooltip-url", default=TOOLTIP_URL, help="URL-Muster fuer Tooltips, {id} als Platzhalter")
    args = ap.parse_args()

    if args.probe:
        name, text = fetch_tooltip(args.probe, args.tooltip_url)
        print("Name   :", name)
        print("Tooltip:", text)
        print("\nSieht das sinnvoll aus (Name + Text wie im Spiel)? Dann laeuft der Rest.")
        return

    out = Path(args.out)
    db = load_pkl(out)
    print(f"Bestehende Datei: {len(db)} Items")

    # 1) Item-Listen einsammeln
    found = {}  # id -> (name, item_type)
    for page in LIST_PAGES:
        url = WOWHEAD_ROOT + page
        try:
            lv = parse_listview(get(url).text)
        except RuntimeError as exc:
            print(f"  !! uebersprungen: {exc}")
            continue
        if lv is None:
            print(f"  !! keine Item-Liste gefunden: {url} (Slug falsch oder Wowhead hat die Seite geaendert)")
            continue
        flag = "  <-- 1000er-Limit erreicht, Liste evtl. unvollstaendig!" if len(lv) >= 1000 else ""
        print(f"  {page}: {len(lv)} Items{flag}")
        for it in lv:
            found[int(it["id"])] = (it.get("name", ""), ITEM_CLASS.get(it.get("classs"), "Other"))
        time.sleep(args.delay)

    if not found:
        sys.exit("Keine Items gefunden - Abbruch (nichts gespeichert).")

    # 2) Tooltips fuer neue Items holen
    todo = [i for i in sorted(found) if args.refresh or i not in db]
    print(f"\n{len(found)} Items insgesamt, {len(todo)} Tooltips zu laden")

    errors = 0
    try:
        for n, item_id in enumerate(todo, 1):
            name, item_type = found[item_id]
            try:
                tip_name, tip = fetch_tooltip(item_id, args.tooltip_url)
            except (RuntimeError, ValueError) as exc:
                errors += 1
                print(f"  !! Item {item_id}: {exc}")
                if errors >= 25 and errors > n // 2:
                    sys.exit("Zu viele Fehler - stimmt die Tooltip-URL? (Test: --probe <id>)")
                continue
            db[item_id] = {
                "item_name": tip_name or name,
                "item_type": item_type,
                "tooltip_single": tip,
            }
            if n % 50 == 0:
                save_pkl(out, db)
                print(f"  {n}/{len(todo)} ...")
            time.sleep(args.delay)
    finally:
        # Auch bei Abbruch (Strg+C) sichern, was schon geholt wurde
        save_pkl(out, db)

    print(f"\nFertig: {len(db)} Items in {out} gespeichert ({errors} Fehler).")


if __name__ == "__main__":
    main()
