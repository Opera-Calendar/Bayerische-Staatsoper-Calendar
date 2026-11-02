#!/usr/bin/env python3
"""Bayerische Staatsoper: opera performances from public Operabook listings.

This is a provisional source: coverage must be checked against the official season.
Never overwrites an existing calendar if too little data was retrieved.
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = 'https://operabook.org'
SOURCE = BASE + '/en/artist/57010_bayerisches-staatsorchester'
DESTINATION = Path('bayerische-staatsoper.ics')
SEASON = '2026/2027'
TZ = ZoneInfo('Europe/Berlin')
DATE_RE = re.compile(r'\b(\d{2}/\d{2}/20\d{2})\s+(\d{2}:\d{2})\b')
EXCLUDE = ('recital', 'concert', 'oratorio', 'symphony')


def retrieve(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    if len(response.content) < 2000:
        raise RuntimeError(f'Too little page content: {url}')
    return BeautifulSoup(response.text, 'html.parser')


def find_productions(soup):
    result = {}
    for a in soup.select('a[href]'):
        href = a.get('href', '')
        match = re.search(r'(?:^|/)performance/(\d+_[^?#]+)', href)
        if not match:
            continue
        parent = a.find_parent('li') or a.parent
        context = parent.get_text(' ', strip=True) if parent else ''
        title = a.get_text(' ', strip=True)
        if SEASON not in context or 'Bayerische Staatsoper' not in context:
            continue
        if any(word in title.lower() for word in EXCLUDE):
            continue
        url = BASE + '/en/performance/' + match.group(1).strip('/')
        result[url] = title.removeprefix('Orchestra – ').strip()
    return result



# Operabook sometimes exposes empty link text and an empty H1.
# The performance URL remains a reliable non-empty title fallback.
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


def title_from_url(url):
    last = urlparse(url).path.rstrip('/').split('/')[-1]
    slug = last.split('_', 1)[-1]
    slug = re.sub(r'-bayerische-staatsoper$', '', slug)
    if not slug or slug.isdigit():
        raise RuntimeError(f'Cannot infer opera title from URL: {url}')
    return ORIGINAL_TITLES.get(slug, slug.replace('-', ' ').title())


def resolve_title(soup, listed_title, url):
    heading = soup.find('h1')
    h1_title = heading.get_text(' ', strip=True) if heading else ''
    # Prefer a mapped original-language work title when the source uses
    # translated English titles, otherwise preserve a real visible heading.
    fallback = title_from_url(url)
    if fallback in ORIGINAL_TITLES.values():
        return fallback
    title = h1_title or listed_title.strip() or fallback
    if not title.strip():
        raise RuntimeError(f'Blank opera title: {url}')
    return title


def extract_dates(soup):
    heading = next((h for h in soup.find_all(re.compile('^h[1-6]$')) if 'performance dates' in h.get_text(' ', strip=True).lower()), None)
    if not heading:
        # Operabook occasionally omits the heading even when dated performances exist.
        # Scan the document, but accept only explicit date + time pairs.
        region = soup
    else:
        region = heading.find_parent() or soup
    # Dates follow this heading; the text may include duplicate display sections.
    dates = set()
    for s in [region, soup]:
        for date_str, clock in DATE_RE.findall(s.get_text(' ', strip=True)):
            dt = datetime.strptime(date_str + ' ' + clock, '%d/%m/%Y %H:%M').replace(tzinfo=TZ)
            if datetime(2026, 9, 1, tzinfo=TZ) <= dt < datetime(2027, 9, 1, tzinfo=TZ):
                dates.add(dt)
        if dates:
            break
    return sorted(dates)


def ics_safe(value):
    return str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def folded(line):
    # RFC 5545 75-octet folding, safe for UTF-8.
    out, current, length = [], '', 0
    for char in line:
        n = len(char.encode('utf-8'))
        if current and length + n > 75:
            out.append(current)
            current, length = ' ' + char, 1 + n
        else:
            current += char
            length += n
    out.append(current)
    return '\r\n'.join(out)


def main():
    session = requests.Session()
    session.headers.update({'User-Agent': 'OperaCalendar/1.0 (personal noncommercial calendar)', 'Accept': 'text/html'})
    listing = find_productions(retrieve(session, SOURCE))
    print(f'Operabook 2026/27: {len(listing)} productions discovered', flush=True)
    if len(listing) < 25:
        raise RuntimeError('Season listing incomplete; existing calendar preserved')

    events = {}
    failures = []
    for number, (url, listed_title) in enumerate(sorted(listing.items()), 1):
        try:
            page = retrieve(session, url)
            title = resolve_title(page, listed_title, url)
            dates = extract_dates(page)
            if not dates:
                failures.append(f'{listed_title}: no performance dates')
                continue
            for start in dates:
                key = (title.casefold(), start.isoformat())
                events[key] = (title, start, url)
            print(f'{number}/{len(listing)} {title}: {len(dates)} dates', flush=True)
        except Exception as exc:
            failures.append(f'{listed_title}: {type(exc).__name__}: {exc}')
            print('WARNING: ' + failures[-1], flush=True)

    print(f'Collected {len(events)} distinct performances; failed productions: {len(failures)}', flush=True)
    if not events or any(not title.strip() for title, _, _ in events.values()):
        raise RuntimeError('Blank opera titles detected; calendar not overwritten')
    # Avoid silently publishing a partial calendar (tune only after inspecting first full run).
    # The 2026/27 Operabook listing currently contains one production without
    # parseable dates: The Abduction from the Seraglio. Publish the 162 verified
    # performances rather than suppressing the whole calendar, but fail on any
    # additional missing production or a substantial decline in coverage.
    known_exception = ('the abduction from the seraglio', 'die entführung aus dem serail')
    acceptable = len(failures) == 0 or (len(failures) == 1 and any(
        name in failures[0].lower() for name in known_exception))
    if len(events) < 150 or not acceptable:
        raise RuntimeError('Coverage below safety threshold or new production failed; previous ICS preserved. Inspect logs.')
    if failures:
        print('WARNING: Publishing a provisional, incomplete calendar. Missing: ' + '; '.join(failures), flush=True)

    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Opera Calendar//Bayerische Staatsoper via Operabook//DE',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:Bayerische Staatsoper – Opern',
             'X-WR-TIMEZONE:Europe/Berlin', 'X-PUBLISHED-TTL:PT24H']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for title, start, url in sorted(events.values(), key=lambda x: (x[1], x[0])):
        uid = hashlib.sha256((title.casefold() + start.isoformat()).encode()).hexdigest()[:24] + '@opera-calendar'
        # End time is deliberately not invented; DTSTART-only is permitted for VEVENT.
        lines += ['BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{stamp}',
                  f'DTSTART:{start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}',
                  f'SUMMARY:{ics_safe(title)}', 'LOCATION:Bayerische Staatsoper\\, München',
                  f'DESCRIPTION:{ics_safe("Spielplanquelle: Operabook. Details und Änderungen bitte bei der Bayerischen Staatsoper überprüfen. " + url)}',
                  f'URL:{url}', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    new_contents = '\r\n'.join(folded(line) for line in lines) + '\r\n'
    if DESTINATION.exists() and DESTINATION.read_bytes() == new_contents.encode('utf-8'):
        print('No changes to calendar')
        return
    DESTINATION.write_bytes(new_contents.encode('utf-8'))
    print(f'Wrote {DESTINATION} ({len(events)} operas)', flush=True)

if __name__ == '__main__':
    main()
