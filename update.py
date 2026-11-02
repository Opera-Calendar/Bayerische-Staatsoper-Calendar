"""Generate two Munich opera iCal subscriptions from official monthly programme pages."""
import hashlib
import re
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import holidays
import requests
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta

BASE = 'https://www.staatsoper.de'
TZ = ZoneInfo('Europe/Berlin')
MONTHS_AHEAD = 13
EVENT_URL_RE = re.compile(r'/stuecke/[^/]+/(20\d{2})-(\d{2})-(\d{2})-(\d{2})(\d{2})-\d+')
TITLE_RE = re.compile(r'Weitere Informationen zu\s+(.+?)\s+anzeigen', re.I | re.S)
CATEGORY_RE = re.compile(r'(?:^|\s)(Oper|Ballett|Konzert|Extra|Kind&Co)(?:\s|$)', re.I)
VENUES = ('Nationaltheater', 'Prinzregententheater', 'Cuvilliés-Theater', 'Reithalle', 'Allerheiligen-Hofkirche', 'Marstall')


def ics_escape(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold_line(line):
    result, current = [], ''
    for char in line:
        if len((current + char).encode('utf-8')) > 73:
            result.append(current)
            current = ' ' + char
        else:
            current += char
    result.append(current)
    return result


def finish(lines):
    return ('\r\n'.join(segment for line in lines for segment in fold_line(line)) + '\r\n').encode('utf-8')


def event_details(anchor):
    """Return only official Opera category event links with a date embedded in URL."""
    href = anchor.get('href', '')
    match = EVENT_URL_RE.search(href)
    if not match or href.endswith('/calendar.ics'):
        return None
    year, month, day, hour, minute = map(int, match.groups())
    start = datetime(year, month, day, hour, minute, tzinfo=TZ)

    # Event cards have an accessible label ending with the performance category.
    # Inspect the event link first; only use a narrow parent if category is external.
    own_text = anchor.get_text(' ', strip=True)
    own_label = ' '.join([own_text, anchor.get('aria-label', ''), anchor.get('title', '')])
    label = own_label
    if not re.search(r'\bOper\s*$', own_label, re.I):
        # Category may be a sibling of the link rather than part of the link.
        # Require a parent containing this link and at most one event detail link.
        container = anchor.parent
        found = False
        for _ in range(4):
            if container is None:
                break
            links = [a for a in container.select('a[href*="/stuecke/"]')
                     if EVENT_URL_RE.search(a.get('href', ''))]
            if len(links) == 1:
                full = container.get_text(' ', strip=True)
                if 'Oper' in list(container.stripped_strings) and not any(x in list(container.stripped_strings) for x in ('Ballett', 'Konzert', 'Extra', 'Kind&Co')):
                    label = full
                    found = True
                    break
            container = container.parent
        if not found:
            return None

    title_match = TITLE_RE.search(label)
    if not title_match:
        # If the label is assembled from sibling nodes, look for the title there.
        return None
    title = re.sub(r'\s+', ' ', title_match.group(1)).strip()
    if not title or len(title) > 180:
        return None
    venue = next((v for v in VENUES if v.lower() in label.lower()), 'Bayerische Staatsoper')
    return {'title': title, 'start': start, 'venue': venue + ', München',
            'url': urljoin(BASE, href.split('?')[0])}


def fetch_events():
    session = requests.Session()
    session.headers.update({'User-Agent': 'OperaCalendar/1.0 (public programme subscription)',
                            'Accept-Language': 'de-DE,de;q=0.9'})
    first = datetime.now(TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    events = {}
    failed = []
    for offset in range(MONTHS_AHEAD):
        month = first + relativedelta(months=offset)
        url = f'{BASE}/spielplan/{month:%Y-%m}'
        response = session.get(url, timeout=45)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        # A month must actually be displayed, not a fallback or error page.
        month_names = ('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
                       'August', 'September', 'Oktober', 'November', 'Dezember')
        expected = f'{month_names[month.month-1]} {month.year}'
        if expected not in soup.get_text(' ', strip=True):
            failed.append(f'{month:%Y-%m} (incorrect month heading)')
            continue
        count = 0
        for anchor in soup.select('a[href*="/stuecke/"]'):
            ev = event_details(anchor)
            if ev is None:
                continue
            if ev['start'].year != month.year or ev['start'].month != month.month:
                continue
            key = (ev['start'].isoformat(), ev['url'])
            if key not in events:
                count += 1
            events[key] = ev
        print(f'{month:%Y-%m}: {count} opera performances; total {len(events)}', flush=True)
        # The season may have a summer break, but an empty first month is suspicious.
        if offset == 0 and count == 0:
            failed.append(f'{month:%Y-%m} (zero opera performances)')
    if failed or len(events) < 10:
        raise RuntimeError(f'Aborting to protect existing calendars: total={len(events)}, problems={failed}')
    return sorted(events.values(), key=lambda e: e['start'])


def sale_start(performance, by_holidays):
    date = performance.date() - relativedelta(months=2)
    while date.weekday() == 6 or date in by_holidays:
        date -= timedelta(days=1)
    return datetime.combine(date, time(10, 0), tzinfo=TZ)


def make_calendar(events, sales=False):
    kind = 'Vorverkaufsbeginn' if sales else 'Opernvorstellungen'
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'CALSCALE:GREGORIAN',
             'PRODID:-//Opera Schedules//Bayerische Staatsoper//DE',
             'METHOD:PUBLISH', f'X-WR-CALNAME:Bayerische Staatsoper - {kind}',
             'X-WR-TIMEZONE:Europe/Berlin', 'REFRESH-INTERVAL;VALUE=DURATION:P1D',
             'X-PUBLISHED-TTL:PT24H']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    years = range(min(e['start'].year for e in events)-1, max(e['start'].year for e in events)+1)
    by_holidays = holidays.country_holidays('DE', subdiv='BY', years=years)
    for ev in events:
        start = sale_start(ev['start'], by_holidays) if sales else ev['start']
        end = start + timedelta(minutes=15 if sales else 1)
        title = ('Vorverkauf: ' if sales else '') + ev['title']
        uid = hashlib.sha256((ev['url'] + kind).encode()).hexdigest()[:28] + '@opera-schedules'
        description = (f'Vorverkauf für {ev["start"]:%d.%m.%Y %H:%M} Uhr.' if sales
                       else 'Aufführungsdauer nicht verifiziert; Termin markiert den Beginn.')
        description += '\nQuelle: ' + ev['url']
        lines += ['BEGIN:VEVENT', 'UID:' + uid, 'DTSTAMP:' + stamp,
                  f'DTSTART;TZID=Europe/Berlin:{start:%Y%m%dT%H%M%S}',
                  f'DTEND;TZID=Europe/Berlin:{end:%Y%m%dT%H%M%S}',
                  'SUMMARY:' + ics_escape(title),
                  'LOCATION:' + ics_escape('Online / Bayerische Staatsoper' if sales else ev['venue']),
                  'DESCRIPTION:' + ics_escape(description), 'URL:' + ev['url']]
        if sales:
            alarm_dates = [start - relativedelta(months=3), start - relativedelta(months=1),
                           start - timedelta(weeks=1), start - timedelta(days=1)]
            for when in alarm_dates:
                lines += ['BEGIN:VALARM', 'ACTION:DISPLAY',
                          'DESCRIPTION:' + ics_escape(title),
                          'TRIGGER;VALUE=DATE-TIME:' + when.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                          'END:VALARM']
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return finish(lines)


if __name__ == '__main__':
    operas = fetch_events()
    for name, sales in [('bayerische-staatsoper.ics', False),
                        ('bayerische-staatsoper-vorverkauf.ics', True)]:
        data = make_calendar(operas, sales)
        Path(name).write_bytes(data)
        print(f'Created {name} ({len(operas)} events)', flush=True)
