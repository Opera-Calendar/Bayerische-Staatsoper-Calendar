"""Prueft den Zugriff von GitHub Actions auf alternative Spielplanquellen.
Keine Kalenderdateien werden erstellt oder veraendert.
"""
import re
import sys
import requests
from bs4 import BeautifulSoup

SOURCES = {
    "Operabook / Un ballo in maschera": "https://operabook.org/en/performance/68429_a-masked-ball-bayerische-staatsoper",
    "Operabook / Siegfried": "https://operabook.org/en/performance/68411_siegfried-bayerische-staatsoper",
    "Operabook / Maria Stuarda": "https://operabook.org/en/performance/68412_mary-stuart-bayerische-staatsoper",
}

session = requests.Session()
session.headers.update({"User-Agent": "OperaCalendar/1.0 (personal calendar; polite availability check)", "Accept": "text/html"})
passed = 0
for label, url in SOURCES.items():
    print(f"\nQuelle: {label}\nURL: {url}", flush=True)
    try:
        r = session.get(url, timeout=20)
        print(f"HTTP: {r.status_code}; Bytes: {len(r.content)}; Ziel: {r.url}", flush=True)
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        dates = sorted(set(re.findall(r"\b\d{2}/\d{2}/20(?:26|27)\s+\d{2}:\d{2}\b", text)))
        print(f"Gefundene Auffuehrungsdaten: {len(dates)}", flush=True)
        print("Beispiele:", ", ".join(dates[:5]), flush=True)
        if "Bayerische Staatsoper" in text and dates:
            passed += 1
    except requests.RequestException as e:
        print(f"Netzwerkfehler: {type(e).__name__}: {e}", flush=True)

print(f"\nERGEBNIS: {passed}/{len(SOURCES)} Operabook-Seiten mit Terminen lesbar.", flush=True)
if passed < 2:
    print("Noch keine belastbare Datenquelle. Kein Kalender wurde veraendert.", flush=True)
    sys.exit(1)
print("Operabook ist fuer GitHub erreichbar. Als naechstes Vollstaendigkeit des Spielplans pruefen.", flush=True)
