#!/usr/bin/env python3
"""Bayerische Staatsoper: automatic iCal subscription via Operabook.

Performance dates and starting times are taken from Operabook. End times may be
estimates. Never replace a previously published calendar after a failed scrape.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = 'https://operabook.org'
SOURCE = BASE + '/en/artist/57010_bayerisches-staatsorchester'
DESTINATION = Path('bayerische-staatsoper.ics')
TZ = ZoneInfo('Europe/Berlin')
DATE_RE = re.compile(r'\b(\d{2}/\d{2}/20\d{2})\s+(\d{2}:\d{2})\b')
LINK_RE = re.compile(r'(?:^|/)performance/(\d+_[^?#]+)')
EXCLUDE = ('recital', 'concert', 'oratorio', 'symphony')
MIN_CURRENT_PRODUCTIONS = 25
MIN_CURRENT_EVENTS = 140

# Only verified for these particular Munich productions. Other ends are estimates.
VERIFIED_DURATION_MINUTES = {
    'un ballo in maschera': 175,
    'siegfried': 330,
}
DEFAULT_DURATION_MINUTES = 180

# Confirmed on the official May 2027 monthly programme. The Operabook page
# for this production currently has no machine-readable dates.
# These are a transparent, season-limited supplement, not a replacement feed.
OFFICIAL_SUPPLEMENTS = {
    2026: {
        'Die Entführung aus dem Serail': {
            'dates': ('2027-05-06 19:00', '2027-05-08 18:00', '2027-05-11 19:00'),
            'url': 'https://www.staatsoper.de/spielplan/2027-05',
        },
    },
}
ORIGINAL_TITLES = {
    'a-masked-ball': 'Un ballo in maschera',
    'the-magic-flute': 'Die Zauberflöte',
    'the-abduction-from-the-seraglio': 'Die Entführung aus dem Serail',
    'the-marriage-of-figaro': 'Le nozze di Figaro',
    'the-barber-of-seville': 'Il barbiere di Siviglia',
    'la-boheme': 'La bohème',
    'ariadne-on-naxos': 'Ariadne auf Naxos',
    'cinderella': 'La Cenerentola',
    'mary-stuart': 'Maria Stuarda',
    'werther': 'Werther',
    'siegfried': 'Siegfried',
}


def season_start_year(today=None):
    today = today or date.today()
    return today.year if today.month >= 9 else today.year - 1


def season_label(year):
    return f'{year}/{year + 1}'


def retrieve(session, url):
    response = session.get(url, timeout=35)
    response.raise_for_status()
    if len(response.content) < 2000:
        raise RuntimeError(f'Too little content at {url}')
    return BeautifulSoup(response.text, 'html.parser')


def find_productions(soup, wanted_seasons):
    """Keep the original, proven Operabook artist-listing extraction.

    The formerly failing version tried to discover seasons separately. We
    instead select known current/next season labels in the listing itself.
    """
    found = {}
    for a in soup.select('a[href]'):
        match = LINK_RE.search(a.get('href', ''))
        if not match:
            continue
        parent = a.find_parent('li') or a.parent
        context = parent.get_text(' ', strip=True) if parent else ''
        title = a.get_text(' ', strip=True)
        matched_seasons = [year for year in wanted_seasons if season_label(year) in context]
        if not matched_seasons or 'Bayerische Staatsoper' not in context:
            continue
        if any(word in title.lower() for word in EXCLUDE):
            continue
        url = BASE + '/en/performance/' + match.group(1).strip('/')
        found[url] = (title.removeprefix('Orchestra – ').strip(), set(matched_seasons))
    return found


def title_from_url(url):
    last = urlparse(url).path.rstrip('/').split('/')[-1]
    slug = re.sub(r'-bayerische-staatsoper$', '', last.split('_', 1)[-1])
    if not slug or slug.isdigit():
        raise RuntimeError(f'No title in URL {url}')
    return ORIGINAL_TITLES.get(slug, slug.replace('-', ' ').title())


def resolve_title(soup, listed_title, url):
    fallback = title_from_url(url)
    if fallback in ORIGINAL_TITLES.values():
        return fallback
    heading = soup.find('h1')
    title = (heading.get_text(' ', strip=True) if heading else '') or listed_title or fallback
    if not title.strip():
        raise RuntimeError(f'Blank title at {url}')
    return title.strip()


def extract_dates(soup, season_years):
    heading = next((h for h in soup.find_all(re.compile('^h[1-6]$'))
                    if 'performance dates' in h.get_text(' ', strip=True).lower()), None)
    region = heading.find_parent() if heading else soup
    dates = set()
    for node in (region, soup):
        for day, clock in DATE_RE.findall(node.get_text(' ', strip=True)):
            dt = datetime.strptime(day + ' ' + clock, '%d/%m/%Y %H:%M').replace(tzinfo=TZ)
            if any(datetime(y, 9, 1, tzinfo=TZ) <= dt < datetime(y+1, 9, 1, tzinfo=TZ)
                   for y in season_years):
                dates.add(dt)
        if dates:
            break
    return sorted(dates)


def ics_safe(value):
    return str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def folded(line):
    result, current, length = [], '', 0
    for char in line:
        size = len(char.encode('utf-8'))
        if current and length + size > 75:
            result.append(current)
            current, length = ' ' + char, 1 + size
        else:
            current += char
            length += size
    return '\r\n'.join(result + [current])


def main():
    now = date.today()
    current_year = season_start_year(now)
    # Include the current season and the next season once Operabook lists it.
    # During July/August, also keep the just-finished season if relevant.
    wanted = [current_year, current_year + 1]
    print('Checking seasons:', ', '.join(season_label(y) for y in wanted), flush=True)
    session = requests.Session()
    session.headers.update({'User-Agent': 'OperaCalendar/1.0 (personal noncommercial calendar)',
                            'Accept': 'text/html'})
    found = find_productions(retrieve(session, SOURCE), wanted)
    current_list = {url: info for url, info in found.items() if current_year in info[1]}
    print(f'Current season: {len(current_list)} productions; total: {len(found)}', flush=True)
    if len(current_list) < MIN_CURRENT_PRODUCTIONS:
        raise RuntimeError('Too few current season productions; previous ICS preserved')

    events = {}
    failed = []
    for number, (url, (listed_title, years)) in enumerate(sorted(found.items()), 1):
        try:
            page = retrieve(session, url)
            title = resolve_title(page, listed_title, url)
            dates = extract_dates(page, years)
            if not dates:
                failed.append((url, title, years, 'no dates'))
                print(f'WARNING {title}: no dates', flush=True)
                continue
            for start in dates:
                # Keep existing event IDs for the current season wherever possible.
                key = (title.casefold(), start.isoformat())
                events[key] = (title, start, url)
            print(f'{number}/{len(found)} {title}: {len(dates)} dates', flush=True)
        except Exception as exc:
            failed.append((url, listed_title, years, str(exc)))
            print(f'WARNING {listed_title}: {exc}', flush=True)

    # Add officially verified exceptions only for their exact season, without
    # duplicating an event if Operabook later starts publishing its dates.
    for season_year in wanted:
        for title, supplement in OFFICIAL_SUPPLEMENTS.get(season_year, {}).items():
            for value in supplement['dates']:
                start = datetime.strptime(value, '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
                if not (datetime(season_year, 9, 1, tzinfo=TZ) <= start
                        < datetime(season_year + 1, 9, 1, tzinfo=TZ)):
                    raise RuntimeError(f'Official supplement outside its season: {value}')
                key = (title.casefold(), start.isoformat())
                if key not in events:
                    events[key] = (title, start, supplement['url'])
                    print(f'OFFICIAL SUPPLEMENT {title}: {value}', flush=True)

    current_events = [event for event in events.values()
                      if datetime(current_year, 9, 1, tzinfo=TZ) <= event[1]
                      < datetime(current_year + 1, 9, 1, tzinfo=TZ)]
    print(f'Found {len(events)} distinct performances ({len(current_events)} current-season), '
          f'{len(failed)} problem productions', flush=True)
    if len(current_events) < MIN_CURRENT_EVENTS:
        raise RuntimeError('Too few current-season performances; previous ICS preserved')
    if any(not event[0].strip() for event in events.values()):
        raise RuntimeError('Blank opera title found; previous ICS preserved')

    exceptions = {'the-abduction-from-the-seraglio', 'die entführung aus dem serail'}
    current_failures = [item for item in failed if current_year in item[2]]
    unexpected = [item for item in current_failures
                  if not any(name in (item[0] + ' ' + item[1]).lower() for name in exceptions)]
    if unexpected or len(current_failures) > 1:
        raise RuntimeError('Unexpected missing current-season production; previous ICS preserved')
    if failed:
        print('WARNING: Some productions could not be parsed; verify coverage.', flush=True)

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0',
             'PRODID:-//Opera Calendar//Bayerische Staatsoper via Operabook//DE',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             'X-WR-CALNAME:Bayerische Staatsoper – Opern',
             'X-WR-TIMEZONE:Europe/Berlin', 'X-PUBLISHED-TTL:PT24H']
    for title, start, url in sorted(events.values(), key=lambda event: (event[1], event[0])):
        uid = hashlib.sha256((title.casefold() + start.isoformat()).encode()).hexdigest()[:24] + '@opera-calendar'
        duration = VERIFIED_DURATION_MINUTES.get(title.casefold())
        minutes = duration if duration is not None else DEFAULT_DURATION_MINUTES
        finish = start + timedelta(minutes=minutes)
        note = (f'Ungefähre Aufführungsdauer: {minutes // 60} h {minutes % 60:02d} min.'
                if duration is not None else 'Endzeit geschätzt: 3 Stunden (nicht bestätigt).')
        source_note = ('Datum und Anfangszeit: offizieller Staatsoper-Spielplan.'
                       if 'staatsoper.de' in url else 'Datum und Anfangszeit: Operabook.')
        description = f'{note} {source_note} Änderungen bei der Staatsoper prüfen. {url}'
        lines.extend(['BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{stamp}',
                      f'DTSTART:{start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}',
                      f'DTEND:{finish.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}',
                      f'SUMMARY:{ics_safe(title)}', 'LOCATION:Bayerische Staatsoper\\, München',
                      f'DESCRIPTION:{ics_safe(description)}', f'URL:{url}', 'END:VEVENT'])
    lines.append('END:VCALENDAR')
    contents = '\r\n'.join(folded(line) for line in lines) + '\r\n'
    if DESTINATION.exists() and DESTINATION.read_bytes() == contents.encode('utf-8'):
        print('Calendar unchanged', flush=True)
        return
    DESTINATION.write_bytes(contents.encode('utf-8'))
    print(f'Published {len(events)} events to {DESTINATION}', flush=True)


if __name__ == '__main__':
    main()
