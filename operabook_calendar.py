#!/usr/bin/env python3
"""Rolling Bayerische Staatsoper opera calendar, based on public Operabook listings.

Priority: work, performance DATE and START TIME. End times can be approximate.
Existing calendar is retained on source errors or suspicious coverage drops.
This does not include a ticket-sales calendar.
"""

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = "https://operabook.org"
SOURCE = BASE + "/en/artist/57010_bayerisches-staatsorchester"
DESTINATION = Path("bayerische-staatsoper.ics")
TZ = ZoneInfo("Europe/Berlin")
DATE_RE = re.compile(r"\b(\d{2}/\d{2}/20\d{2})\s+(\d{2}:\d{2})\b")
SEASON_RE = re.compile(r"\b(20\d{2})\s*/\s*(20\d{2})\b")
EXCLUDE = ("recital", "concert", "oratorio", "symphony")

# Durations are tied to a specific work/production, NEVER to the composer.
# These two entries were previously checked for the 2026/27 Munich production.
KNOWN_2026_DURATIONS = {
    "un ballo in maschera": 175,
    "siegfried": 330,
}
APPROXIMATE_DURATION_MINUTES = 180
MIN_PRODUCTIONS_IN_MAIN_SEASON = 20
MIN_EVENTS_IN_MAIN_SEASON = 80

TITLES_FROM_SLUG = {
    "a-masked-ball": "Un ballo in maschera",
    "the-magic-flute": "Die Zauberflöte",
    "the-abduction-from-the-seraglio": "Die Entführung aus dem Serail",
    "the-marriage-of-figaro": "Le nozze di Figaro",
    "the-barber-of-seville": "Il barbiere di Siviglia",
    "la-boheme": "La bohème",
    "ariadne-on-naxos": "Ariadne auf Naxos",
    "cinderella": "La Cenerentola",
    "mary-stuart": "Maria Stuarda",
    "twilight-of-the-gods": "Götterdämmerung",
    "the-flying-dutchman": "Der fliegende Holländer",
    "the-knight-of-the-rose": "Der Rosenkavalier",
    "the-elixir-of-love": "L'elisir d'amore",
    "the-queen-of-spades": "Pique Dame",
    "the-bat": "Die Fledermaus",
    "siegfried": "Siegfried",
}


def season_start(now):
    """Munich opera season convention: September to August."""
    return now.year if now.month >= 9 else now.year - 1


def fetch(session, url):
    response = session.get(url, timeout=35)
    response.raise_for_status()
    if len(response.content) < 2000:
        raise RuntimeError(f"Unexpectedly short source page ({len(response.content)} bytes): {url}")
    return BeautifulSoup(response.text, "html.parser")


def find_productions(soup, current_season):
    """Derive season from EACH listing row, not a hard-coded season year."""
    productions = {}
    seasons = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not re.search(r"/performance/\d+_[^?#]+", href):
            continue
        container = a.find_parent("li") or a.parent
        context = container.get_text(" ", strip=True) if container else ""
        if "Bayerische Staatsoper" not in context:
            continue
        match = SEASON_RE.search(context)
        if not match:
            continue
        y1, y2 = map(int, match.groups())
        if y2 != y1 + 1 or not (current_season <= y1 <= current_season + 1):
            continue
        raw_title = a.get_text(" ", strip=True).removeprefix("Orchestra – ").strip()
        if any(item in raw_title.casefold() for item in EXCLUDE):
            continue
        url = urljoin(BASE, href.split("#")[0].split("?")[0])
        # Exact production URL acts as stable identity, not the work title.
        productions[url] = (raw_title, y1)
        seasons.add(y1)
    return productions, seasons


def title_from_url(url):
    slug = urlparse(url).path.rstrip("/").split("/")[-1].split("_", 1)[-1]
    slug = re.sub(r"-bayerische-staatsoper$", "", slug)
    if not slug or slug.isdigit():
        raise ValueError(f"No work title in URL: {url}")
    return TITLES_FROM_SLUG.get(slug, slug.replace("-", " ").title())


def work_title(soup, listed, url):
    slug_title = title_from_url(url)
    slug = urlparse(url).path.rstrip("/").split("/")[-1].split("_", 1)[-1]
    slug = re.sub(r"-bayerische-staatsoper$", "", slug)
    # Restore original-language work titles where Operabook uses English translations.
    if slug in TITLES_FROM_SLUG:
        return slug_title
    h1 = soup.find("h1")
    heading = h1.get_text(" ", strip=True) if h1 else ""
    candidate = heading or listed or slug_title
    if not candidate.strip():
        raise ValueError(f"Empty work title: {url}")
    return candidate.strip()


def performance_dates(soup, season):
    """Only accept explicit DD/MM/YYYY HH:MM source data in matching season."""
    text = soup.get_text(" ", strip=True)
    result = set()
    for day_text, time_text in DATE_RE.findall(text):
        dt = datetime.strptime(day_text + " " + time_text, "%d/%m/%Y %H:%M").replace(tzinfo=TZ)
        if date(season, 9, 1) <= dt.date() < date(season + 1, 9, 1):
            result.add(dt)
    return sorted(result)


def escape_ics(text):
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def fold_ics(line):
    pieces = []
    current, byte_length = "", 0
    for ch in line:
        size = len(ch.encode("utf-8"))
        if current and byte_length + size > 75:
            pieces.append(current)
            current, byte_length = " " + ch, 1 + size
        else:
            current += ch
            byte_length += size
    pieces.append(current)
    return "\r\n".join(pieces)


def existing_event_count(season):
    if not DESTINATION.exists():
        return 0
    try:
        data = DESTINATION.read_text(encoding="utf-8-sig")
        # Existing files use UTC DTSTART; count roughly by season year, including
        # UTC crossing midnight where applicable.
        starts = re.findall(r"^DTSTART(?:;[^:]*)?:(\d{8})T\d{6}Z?", data, re.M)
        return sum(date(season, 8, 31) < datetime.strptime(x, "%Y%m%d").date() < date(season + 1, 9, 2) for x in starts)
    except Exception as exc:
        print(f"WARNING: Could not parse previous event count: {exc}", flush=True)
        return 0


def duration_for(title, season):
    verified = KNOWN_2026_DURATIONS.get(title.casefold()) if season == 2026 else None
    if verified is not None:
        return verified, "Produktion 2026/27: zuvor geprüfte ungefähre Dauer inkl. Pausen."
    return APPROXIMATE_DURATION_MINUTES, "Endzeit nur geschätzt (3 Stunden); Datum und Anfangszeit stammen aus Operabook."


def render(events):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Opera Calendar//Bayerische Staatsoper via Operabook//DE",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Bayerische Staatsoper – Opern",
        "X-WR-TIMEZONE:Europe/Berlin", "X-PUBLISHED-TTL:PT24H",
    ]
    for (url, local_day), (title, start, season) in sorted(events.items(), key=lambda kv: (kv[1][1], kv[1][0])):
        minutes, note = duration_for(title, season)
        end = start + timedelta(minutes=minutes)
        # Stable UID across updates to an event's START TIME: source production + local DATE.
        uid = hashlib.sha256(f"{url}|{local_day}".encode("utf-8")).hexdigest()[:24] + "@opera-calendar"
        desc = f"{note} Aufführungsdaten: Operabook. Änderungen bei der Bayerischen Staatsoper prüfen. {url}"
        lines += [
            "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
            f"DTSTART:{start.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{escape_ics(title)}",
            "LOCATION:Bayerische Staatsoper\\, München",
            f"DESCRIPTION:{escape_ics(desc)}", f"URL:{url}", "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold_ics(line) for line in lines) + "\r\n").encode("utf-8")


def main():
    season = season_start(datetime.now(TZ))
    session = requests.Session()
    session.headers.update({"User-Agent": "OperaCalendar/1.1 (noncommercial subscription)", "Accept": "text/html"})
    productions, listed_seasons = find_productions(fetch(session, SOURCE), season)
    counts = {s: sum(x[1] == s for x in productions.values()) for s in listed_seasons}
    print(f"Seasons detected: {sorted(listed_seasons)}; productions: {counts}", flush=True)
    if counts.get(season, 0) < MIN_PRODUCTIONS_IN_MAIN_SEASON:
        raise RuntimeError("Current season has too few productions; existing ICS preserved")

    events = {}
    errors = []
    for index, (url, (listed_title, production_season)) in enumerate(sorted(productions.items()), 1):
        try:
            page = fetch(session, url)
            title = work_title(page, listed_title, url)
            dates = performance_dates(page, production_season)
            if not dates:
                errors.append(f"{title}: no dated performances")
                continue
            for start in dates:
                events[(url, start.date().isoformat())] = (title, start, production_season)
            print(f"{index}/{len(productions)} {title} ({production_season}): {len(dates)}", flush=True)
        except Exception as exc:
            errors.append(f"{listed_title or url}: {type(exc).__name__}: {exc}")
            print("WARNING " + errors[-1], flush=True)

    current_count = sum(value[2] == season for value in events.values())
    old_count = existing_event_count(season)
    print(f"Current-season events: {current_count}; previous: {old_count}; total: {len(events)}", flush=True)
    if current_count < MIN_EVENTS_IN_MAIN_SEASON:
        raise RuntimeError("Too few current-season dates; existing ICS preserved")
    if old_count and current_count < old_count * .75:
        raise RuntimeError("Calendar lost more than 25% of dates; existing ICS preserved")
    # Known one-off absence of dates for this Operabook listing.
    known_exception = ("abduction from the seraglio", "entführung aus dem serail")
    unexpected = [e for e in errors if not any(x in e.lower() for x in known_exception)]
    if unexpected:
        raise RuntimeError("New retrieval failures; existing ICS preserved: " + "; ".join(unexpected[:5]))
    if errors:
        print("WARNING: known source omission: " + "; ".join(errors), flush=True)
    if any(not val[0].strip() for val in events.values()):
        raise RuntimeError("Blank work title detected; existing ICS preserved")

    contents = render(events)
    if DESTINATION.exists() and DESTINATION.read_bytes() == contents:
        print("Calendar unchanged", flush=True)
        return
    DESTINATION.write_bytes(contents)
    print(f"Wrote {DESTINATION} with {len(events)} performances", flush=True)


if __name__ == "__main__":
    main()
