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
    "trinkets",   # noetig fuer Muffin.Gear.Trinket.Usable/Nonusable (Tooltip "Use:")
]

# Lueckenlose Abdeckung: zusaetzlich JEDE Unterklasse per Nummer abfragen
# (Wowhead-Format  items=<klasse>.<unterklasse>). Leere Unterklassen werden still
# uebersprungen. So faellt nichts durch, was in keine der Namens-Seiten oben passt.
#   Klasse 0 = Consumable, 7 = Trade Goods, 12 = Quest, 15 = Miscellaneous
SUBCLASS_SWEEP = {0: range(0, 16), 7: range(0, 20), 12: range(0, 3), 15: range(0, 16)}

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

# Ehrlich als Skript auftreten (kein vorgetaeuschter Browser); per --user-agent aenderbar
HEADERS = {"User-Agent": "AutoBarForeverUpdater/1.0 (personal addon data update)"}
TIMEOUT = 60


# ---------------------------------------------------------------- Hilfsfunktionen
GENERIC_PAGE = {0: "items/consumables", 7: "items/trade-goods", 12: "items/quest", 15: "items/miscellaneous"}


def is_fallback(page, lv):
    """Unbekannte Unterklasse: Wowhead zeigt dann die GANZE Klasse statt einer Unterklasse.
    Erkennbar daran, dass die Items nicht alle die angefragte Unterklasse haben."""
    try:
        wanted = int(page.rsplit(".", 1)[-1])
        subs = {int(i["subclass"]) for i in lv}
    except (KeyError, TypeError, ValueError):
        return False  # Feld 'subclass' fehlt -> keine Aussage moeglich
    return bool(subs) and subs != {wanted}


class Throttled(RuntimeError):
    """Wowhead bremst/blockiert (z.B. HTTP 202 = Bot-Schutzseite, 429, 403)."""


THROTTLE_CODES = (202, 403, 429, 503)
CACHE_MAX_AGE = 12 * 3600  # Listenseiten-Cache: 12 Stunden


def get(url, retries=3, delay=2.0):
    """HTTP-GET mit Wiederholungen. Bei Drosselung lange warten, dann Throttled."""
    last, throttled = None, False
    for attempt in range(retries):
        throttled = False
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            throttled = r.status_code in THROTTLE_CODES
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep((30 if throttled else delay) * (attempt + 1))
    if throttled:
        raise Throttled(f"{url}: {last}")
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
    ap.add_argument("--page-delay", type=float, default=2.0, help="Pause zwischen Listenseiten in Sekunden (Standard 2.0)")
    ap.add_argument("--no-cache", action="store_true", help="Listenseiten-Cache ignorieren")
    ap.add_argument("--user-agent", help="eigener User-Agent-Text (Standard: ehrlicher Skript-Name)")
    ap.add_argument("--probe-page", metavar="PFAD", help="nur eine Listenseite testen, z.B. items/consumables/flasks")
    ap.add_argument("--refresh", action="store_true", help="alle Tooltips neu laden, nicht nur neue Items")
    ap.add_argument("--probe", type=int, metavar="ITEM_ID", help="nur einen Tooltip testen und beenden")
    ap.add_argument("--tooltip-url", default=TOOLTIP_URL, help="URL-Muster fuer Tooltips, {id} als Platzhalter")
    args = ap.parse_args()

    if args.user_agent:
        HEADERS["User-Agent"] = args.user_agent

    if args.probe_page:
        r = requests.get(WOWHEAD_ROOT + args.probe_page, headers=HEADERS, timeout=TIMEOUT)
        lv = parse_listview(r.text) if r.status_code == 200 else None
        print(f"HTTP {r.status_code}, {len(r.content)} Bytes, User-Agent: {HEADERS['User-Agent']}")
        print("Item-Liste:", "NICHT gefunden" if lv is None else f"{len(lv)} Items")
        return

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
    page_sets = {}  # Seite -> Menge der Item-IDs (fuer den Abdeckungs-Check)
    sweep_pages = [f"items={c}.{sc}" for c, subs in SUBCLASS_SWEEP.items() for sc in subs]
    cache_path = Path("listpages_cache_forever_v2.json")
    try:
        cache = json.loads(cache_path.read_text()) if cache_path.is_file() and not args.no_cache else {}
    except ValueError:
        cache = {}
    now = time.time()
    skipped_sweep = 0
    for page in LIST_PAGES + sweep_pages:
        is_sweep = page in sweep_pages
        url = WOWHEAD_ROOT + page
        entry = cache.get(page)
        if entry and now - entry["t"] < CACHE_MAX_AGE:
            lv = entry["items"]  # frisch aus dem Cache, keine Anfrage noetig
        else:
            try:
                lv = parse_listview(get(url).text)
            except Throttled as exc:
                sys.exit(f"\n!! Wowhead bremst/blockiert ({exc}).\n"
                         "   Abbruch ohne Aenderungen. Bereits geladene Seiten sind im Cache -\n"
                         "   in 1-2 Stunden einfach erneut starten, es geht dort weiter.")
            except RuntimeError as exc:
                if not is_sweep:
                    print(f"  !! uebersprungen: {exc}")
                continue
            if lv is not None:
                lv = [{"id": int(i["id"]), "name": i.get("name", ""), "classs": i.get("classs"), "subclass": i.get("subclass")} for i in lv]
            cache[page] = {"t": now, "items": lv}
            cache_path.write_text(json.dumps(cache))
            time.sleep(args.page_delay)
        if lv is None:
            if not is_sweep:  # leere Unterklassen im Sweep sind normal
                print(f"  !! keine Item-Liste gefunden: {url} (Slug falsch oder Wowhead hat die Seite geaendert)")
            continue
        if is_sweep and is_fallback(page, lv):
            skipped_sweep += 1
            continue
        capped = len(lv) >= 1000
        flag = "  <-- 1000er-Limit erreicht, Liste evtl. unvollstaendig!" if capped else ""
        print(f"  {page}: {len(lv)} Items{flag}")
        page_sets[page] = {int(it["id"]) for it in lv}
        for it in lv:
            found[int(it["id"])] = (it.get("name", ""), ITEM_CLASS.get(it.get("classs"), "Other"))

    if not any(pg in page_sets for pg in sweep_pages):
        print("\n  !! WARNUNG: KEINE einzige Unterklassen-Seite (items=K.U) lieferte Items.")
        print("     Vermutlich funktioniert dieses URL-Format bei Wowhead Forever nicht -> Sweep wirkungslos!")

    print(f"\nSweep: {skipped_sweep} nicht existierende Unterklassen uebersprungen")
    print("Abdeckung je Klasse:")
    for c, generic in GENERIC_PAGE.items():
        subs = {pg: ids for pg, ids in page_sets.items() if pg.startswith(f"items={c}.")}
        if subs:
            capped = [pg for pg, ids in subs.items() if len(ids) >= 1000]
            total = len(set().union(*subs.values()))
            if capped:
                print(f"  Klasse {c}: UNVOLLSTAENDIG - {', '.join(capped)} am 1000er-Limit ({total} Items bekannt)")
            else:
                print(f"  Klasse {c}: vollstaendig ({total} Items in {len(subs)} Unterklassen)")
        elif generic in page_sets:
            n = len(page_sets[generic])
            print(f"  Klasse {c}: {'UNVOLLSTAENDIG - am 1000er-Limit' if n >= 1000 else 'vollstaendig'} ({n} Items, nur Gesamtseite)")

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
            except Throttled as exc:
                sys.exit(f"\n!! Wowhead bremst/blockiert ({exc}).\n"
                         "   Zwischenstand ist gespeichert - spaeter erneut starten, es geht weiter.")
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
