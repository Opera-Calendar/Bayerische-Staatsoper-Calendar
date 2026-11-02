"""Bayerische Staatsoper calendar generator.

Extracts opera events from the official /spielplan month view using a browser.
Stops rather than writing misleading empty calendars if website structure changes.
"""
import calendar
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

BASE = 'https://www.staatsoper.de/spielplan'
TZ = ZoneInfo('Europe/Berlin')
MONTHS = 13
DATE_RE = re.compile(r'(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+(20\d{2})')
TIME_RE = re.compile(r'(\d{1,2})\.(\d{2})\s*Uhr')
MONTH_NAME = {'Januar':1,'Februar':2,'März':3,'April':4,'Mai':5,'Juni':6,'Juli':7,'August':8,'September':9,'Oktober':10,'November':11,'Dezember':12}


def escape(s):
    return str(s).replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n')


def lines(*items):
    output = []
    for x in items:
        # Fold UTF-8 ICS content lines at 75 octets.
        part = ''
        for ch in x:
            if len((part+ch).encode('utf-8')) > 73:
                output.append(part)
                part = ' ' + ch
            else:
                part += ch
        output.append(part)
    return '\r\n'.join(output) + '\r\n'


def events_from_html(html):
    soup = BeautifulSoup(html,'html.parser')
    result = {}
    for anchor in soup.select('a[href*="/stuecke/"]'):
        href = anchor.get('href','')
        if '/calendar.ics' in href:
            continue
        node = anchor
        raw = anchor.get_text(' ',strip=True)
        # Each full event listing is an anchor with date, time and 'Oper'.
        dm = DATE_RE.search(raw)
        tm = TIME_RE.search(raw)
        if not dm or not tm or not re.search(r'\bOper\b',raw):
            continue
        day, month_word, year = dm.groups()
        month = MONTH_NAME.get(month_word)
        if not month:
            continue
        start = datetime(int(year),month,int(day),int(tm[1]),int(tm[2]),tzinfo=TZ)
        # Link text: date weekday time | venue TITLE composer Preise ...
        tail = raw[tm.end():]
        venue_match = re.search(r'\|\s*([^|]+?)(?:\s*\|\s*PREMIERE)?\s+(?=[A-ZÄÖÜ])',tail)
        venue = 'Bayerische Staatsoper, München'
        if 'Nationaltheater' in tail:
            venue = 'Nationaltheater, München'
        elif 'Prinzregententheater' in tail:
            venue = 'Prinzregententheater, München'
        elif 'Cuvilliés-Theater' in tail:
            venue = 'Cuvilliés-Theater, München'
        title_part = re.sub(r'^.*?\|\s*(?:Nationaltheater|Prinzregententheater|Cuvilliés-Theater)\s*(?:\|\s*PREMIERE\s*)?', '', tail)
        title_part = re.split(r'\s+Preise\b|\s+mehr anzeigen\b',title_part)[0]
        # strip known composer suffixes, avoid corrupting unfamiliar work titles
        composers = ['Giuseppe Verdi','Richard Wagner','Giacomo Puccini','Wolfgang Amadeus Mozart','Gioachino Rossini','Gaetano Donizetti','Richard Strauss','Georg Friedrich Händel','Leoš Janáček','John Adams','Pjotr I. Tschaikowski','Georges Bizet','Charles Gounod','Jules Massenet','Ludwig van Beethoven','Engelbert Humperdinck']
        composer = ''
        for c in composers:
            if title_part.endswith(c):
                composer = c
                title_part = title_part[:-len(c)]
                break
        title = title_part.strip(' |')
        if not title or len(title)>140:
            continue
        url = urljoin(BASE,href)
        key = (start.isoformat(),title)
        result[key] = {'title':title,'composer':composer,'start':start,'url':url,'venue':venue}
    return list(result.values())


def fetch():
    output = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale='de-DE',timezone_id='Europe/Berlin')
        page.goto(BASE,wait_until='domcontentloaded',timeout=60000)
        page.wait_for_timeout(1500)
        # The month picker may use AJAX; navigate through it sequentially.
        visited = set()
        for i in range(MONTHS):
            html = page.content()
            soup = BeautifulSoup(html,'html.parser')
            text = soup.get_text(' ',strip=True)
            month_candidates = re.findall(r'(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+20\d{2}',text)
            new = events_from_html(html)
            for ev in new:
                output[(ev['start'].isoformat(),ev['title'])] = ev
            print(f'Page {i+1}: {len(new)} opera events, {len(output)} total')
            # Move month using visible calendar navigation controls.
            moved = False
            for selector in ['button[aria-label*="Nächster Monat"]','button[aria-label*="nächster Monat"]','button[aria-label*="Next month"]','button[title*="Nächster"]','[data-testid="next-month"]','button:has(svg.lucide-chevron-right)']:
                loc = page.locator(selector)
                if loc.count():
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    moved = True
                    break
            if not moved:
                # Some versions of the site use a date query parameter.
                next_month = (datetime.now(TZ).date().replace(day=1)+relativedelta(months=i+1))
                page.goto(f'{BASE}?date={next_month:%Y-%m-%d}',wait_until='domcontentloaded',timeout=60000)
                page.wait_for_timeout(800)
        browser.close()
    if len(output) < 10:
        raise RuntimeError('Too few operas extracted; website/parser requires inspection. Existing ICS files retained.')
    return sorted(output.values(),key=lambda x:x['start'])


def sale_date(dt, bavaria):
    d = dt.date() - relativedelta(months=2)
    while d.weekday()==6 or d in bavaria:
        d -= timedelta(days=1)
    return datetime.combine(d,time(10,0),tzinfo=TZ)


def serialize(items, kind):
    now = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    buf = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Opera Schedules//Bayerische Staatsoper//DE','CALSCALE:GREGORIAN','METHOD:PUBLISH',f'X-WR-CALNAME:Bayerische Staatsoper – {kind}','X-WR-TIMEZONE:Europe/Berlin','REFRESH-INTERVAL;VALUE=DURATION:P1D','X-PUBLISHED-TTL:PT24H']
    for ev in items:
        start = ev['start'] if kind=='Opernvorstellungen' else sale_date(ev['start'],holidays.country_holidays('DE',subdiv='BY',years=range(ev['start'].year-1,ev['start'].year+2)))
        # Unknown performance duration: do not invent an end time.
        end = start+timedelta(minutes=1 if kind=='Opernvorstellungen' else 15)
        uid = hashlib.sha256((ev['start'].isoformat()+ev['title']+kind).encode()).hexdigest()[:24]+'@opera-schedules'
        title = ev['title'] if kind=='Opernvorstellungen' else 'Vorverkauf: '+ev['title']
        desc = ('Opernvorstellung: '+ev['start'].strftime('%d.%m.%Y %H:%M') if kind=='Vorverkauf' else 'Aufführungsdauer unbekannt; Termin markiert den Beginn.')
        if ev['composer']: desc += '\nKomponist: '+ev['composer']
        desc += '\nQuelle: '+ev['url']
        buf.extend(['BEGIN:VEVENT',f'UID:{uid}',f'DTSTAMP:{now}',f'DTSTART;TZID=Europe/Berlin:{start:%Y%m%dT%H%M%S}',f'DTEND;TZID=Europe/Berlin:{end:%Y%m%dT%H%M%S}',f'SUMMARY:{escape(title)}',f'LOCATION:{escape(ev["venue"] if kind=="Opernvorstellungen" else "Online / Bayerische Staatsoper")}',f'DESCRIPTION:{escape(desc)}',f'URL:{ev["url"]}'])
        if kind!='Opernvorstellungen':
            for alarm_at in [start-relativedelta(months=3),start-relativedelta(months=1),start-timedelta(weeks=1),start-timedelta(days=1)]:
                utc_time = alarm_at.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                buf.extend(['BEGIN:VALARM','ACTION:DISPLAY',f'DESCRIPTION:{escape(title)}',f'TRIGGER;VALUE=DATE-TIME:{utc_time}','END:VALARM'])
        buf.append('END:VEVENT')
    buf.append('END:VCALENDAR')
    return lines(*buf)

if __name__ == '__main__':
    entries = fetch()
    for filename,kind in [('bayerische-staatsoper.ics','Opernvorstellungen'),('bayerische-staatsoper-vorverkauf.ics','Vorverkaufsbeginn')]:
        Path(filename).write_bytes(serialize(entries,kind).encode('utf-8'))
        print('Saved',filename,len(entries),'events')
