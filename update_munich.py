"""Bayerische Staatsoper: two subscription calendars from official monthly programme.
Requires playwright, beautifulsoup4, python-dateutil, holidays.
If site refuses automation, exits without overwriting existing ICS files.
"""
import hashlib
import re
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import holidays
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta
from playwright.sync_api import sync_playwright

BASE = 'https://www.staatsoper.de'
TZ = ZoneInfo('Europe/Berlin')
MONTHS_AHEAD = 13
LINK_RE = re.compile(r'/stuecke/[^/]+/(20\d\d)-(\d\d)-(\d\d)-(\d\d)(\d\d)-\d+')
LABEL_RE = re.compile(r'Weitere Informationen zu\s+(.+?)\s+anzeigen\s*-\s*\d{2}\.\d{2}\.\d{4}', re.I)
VENUES = ('Nationaltheater', 'Prinzregententheater', 'Cuvilliés-Theater', 'Reithalle', 'Allerheiligen-Hofkirche', 'Marstall')
MONTH_NAMES = ('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember')


def esc(s):
    return str(s).replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold(s):
    chunks, current = [], ''
    for c in s:
        if len((current + c).encode('utf-8')) > 73:
            chunks.append(current)
            current = ' ' + c
        else:
            current += c
    return chunks + [current]


def card_is_opera(anchor):
    # Accessible event link usually has category as its final word.
    s = ' '.join((anchor.get_text(' ', strip=True), anchor.get('aria-label', '')))
    if re.search(r'\bOper\s*$', s, flags=re.I):
        return True
    # Category appears as a sibling on some page versions.
    parent = anchor.parent
    for _ in range(4):
        if parent is None:
            break
        links = [a for a in parent.select('a[href*="/stuecke/"]') if LINK_RE.search(a.get('href', ''))]
        if len(links) == 1:
            tokens = list(parent.stripped_strings)
            categories = {'Oper', 'Ballett', 'Konzert', 'Extra', 'Kind&Co'}
            found = set(tokens) & categories
            if found:
                return found == {'Oper'}
        parent = parent.parent
    return False


def parse_month(html, month):
    soup = BeautifulSoup(html, 'html.parser')
    heading = f'{MONTH_NAMES[month.month - 1]} {month.year}'
    if heading not in soup.get_text(' ', strip=True):
        raise RuntimeError(f'Monatsüberschrift fehlt: {heading}')
    results = {}
    for a in soup.select('a[href*="/stuecke/"]'):
        href = a.get('href', '')
        match = LINK_RE.search(href)
        if not match or not card_is_opera(a):
            continue
        year, mon, day, hour, minute = map(int, match.groups())
        if (year, mon) != (month.year, month.month):
            continue
        text = ' '.join((a.get_text(' ', strip=True), a.get('aria-label', ''), a.get('title', '')))
        label = LABEL_RE.search(text)
        if not label:
            print('Warning: no title parsed for', href, flush=True)
            continue
        title = re.sub(r'\s+', ' ', label.group(1)).strip()
        if not 1 < len(title) < 180:
            continue
        start = datetime(year, mon, day, hour, minute, tzinfo=TZ)
        venue = next((v for v in VENUES if v.lower() in text.lower()), 'Bayerische Staatsoper')
        url = urljoin(BASE, href.split('?')[0])
        results[url] = {'title': title, 'start': start, 'venue': venue + ', München', 'url': url}
    return results


def fetch():
    first = datetime.now(TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    found = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(locale='de-DE', timezone_id='Europe/Berlin', viewport={'width': 1440, 'height': 1000})
        for n in range(MONTHS_AHEAD):
            month = first + relativedelta(months=n)
            url = f'{BASE}/spielplan/{month:%Y-%m}'
            response = page.goto(url, wait_until='domcontentloaded', timeout=60000)
            status = response.status if response else 'no response'
            print(f'{month:%Y-%m}: HTTP {status}', flush=True)
            if status != 200:
                browser.close()
                raise RuntimeError(f'Website blockiert auch Browser-Abruf (HTTP {status}) bei {url}. Bestehende ICS bleiben unverändert.')
            page.wait_for_timeout(1200)
            entries = parse_month(page.content(), month)
            print(f'{month:%Y-%m}: {len(entries)} operas', flush=True)
            if n == 0 and not entries:
                raise RuntimeError('Keine Opern im aktuellen Monat erkannt; kein Kalender wird überschrieben.')
            found.update(entries)
        browser.close()
    if len(found) < 10:
        raise RuntimeError(f'Nur {len(found)} Opern gefunden; kein Kalender wird überschrieben.')
    return sorted(found.values(), key=lambda e: e['start'])


def sale_date(event, public_holidays):
    start = event['start']
    # Confirmed 2027 Münchner Opernfestspiele: single special sale day.
    # Official festival performances in late June/July require manual categorization:
    # do not apply a guessed festival date to ordinary performances.
    d = start.date() - relativedelta(months=2)
    while d.weekday() == 6 or d in public_holidays:
        d -= timedelta(days=1)
    return datetime.combine(d, time(10), tzinfo=TZ)


def render(events, sales=False):
    title = 'Vorverkaufsbeginn' if sales else 'Opernvorstellungen'
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             'PRODID:-//Opera Calendar//Bayerische Staatsoper//DE',
             f'X-WR-CALNAME:Bayerische Staatsoper - {title}', 'X-WR-TIMEZONE:Europe/Berlin',
             'REFRESH-INTERVAL;VALUE=DURATION:P1D', 'X-PUBLISHED-TTL:PT24H']
    now = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    years = range(min(e['start'].year for e in events) - 1, max(e['start'].year for e in events) + 1)
    german_holidays = holidays.country_holidays('DE', subdiv='BY', years=years)
    for ev in events:
        start = sale_date(ev, german_holidays) if sales else ev['start']
        end = start + timedelta(minutes=15 if sales else 1)
        label = ('Vorverkauf: ' if sales else '') + ev['title']
        uid = hashlib.sha256((ev['url'] + ('-sale' if sales else '-performance')).encode()).hexdigest()[:32] + '@opera-calendar'
        note = ('Regulärer Vorverkauf: rechnerisch 2 Monate vor Aufführung. ACHTUNG: Sonderfristen für Opernfestspiele und Ja, Mai 2027 können abweichen; offizielle Angaben prüfen.' if sales else 'Aufführungsdauer nicht verifiziert; Termin markiert vorerst nur den Vorstellungsbeginn.')
        lines.extend(['BEGIN:VEVENT', 'UID:' + uid, 'DTSTAMP:' + now,
                      f'DTSTART;TZID=Europe/Berlin:{start:%Y%m%dT%H%M%S}',
                      f'DTEND;TZID=Europe/Berlin:{end:%Y%m%dT%H%M%S}',
                      'SUMMARY:' + esc(label), 'LOCATION:' + esc('Bayerische Staatsoper / online' if sales else ev['venue']),
                      'DESCRIPTION:' + esc(note + '\nQuelle: ' + ev['url']), 'URL:' + ev['url']])
        if sales:
            for when in (start - relativedelta(months=3), start - relativedelta(months=1),
                         start - timedelta(weeks=1), start - timedelta(days=1)):
                lines.extend(['BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:' + esc(label),
                              'TRIGGER;VALUE=DATE-TIME:' + when.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'END:VALARM'])
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return ('\r\n'.join(part for line in lines for part in fold(line)) + '\r\n').encode('utf-8')


if __name__ == '__main__':
    events = fetch()
    for filename, is_sale in (('bayerische-staatsoper.ics', False), ('bayerische-staatsoper-vorverkauf.ics', True)):
        Path(filename).write_bytes(render(events, is_sale))
        print(f'Wrote {filename}: {len(events)} events', flush=True)
