#!/usr/bin/env python3
"""Bayerische Staatsoper: sale calendar plus four *visible* reminder events.

Reads the existing performance ICS. Does not fetch the website or modify
performance dates. Each sale and each reminder has a deterministic UID.
The sale-day rules and festival exceptions are inherited from the existing
Munich sales-calendar implementation (review future season exceptions).
"""
from __future__ import annotations

import calendar
import hashlib
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays

SOURCE = Path('bayerische-staatsoper.ics')
TARGET = Path('bayerische-staatsoper-vorverkauf.ics')
TZ = ZoneInfo('Europe/Berlin')
LINK = 'https://www.staatsoper.de/kartenkauf/'
FESTIVAL_START, FESTIVAL_END = date(2027, 6, 27), date(2027, 7, 31)
FESTIVAL_SALE = date(2027, 3, 25)
JA_MAI_SALE = date(2027, 2, 27)
JA_MAI_TITLES = ('liberty', 'koma', 'die kreide im mund des wolfs')
REMINDERS = (('3 Monate vorher', 3, 0), ('1 Monat vorher', 1, 0),
             ('1 Woche vorher', 0, 7), ('1 Tag vorher', 0, 1))


def unfold(text):
    rows = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line.startswith((' ', '\t')) and rows:
            rows[-1] += line[1:]
        else:
            rows.append(line)
    return rows


def ics_unescape(value):
    # Reverse ICS escaping without losing literal backslashes.
    import re
    return re.sub(r'\\([nN,;\\])', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), value)


def ics_escape(value):
    return (str(value).replace('\\', '\\\\').replace('\n', '\\n')
            .replace(',', '\\,').replace(';', '\\;'))


def fold(line):
    result, chunk, width = [], '', 0
    for c in line:
        n = len(c.encode('utf-8'))
        if chunk and width + n > 75:
            result.append(chunk)
            chunk, width = ' ' + c, n + 1
        else:
            chunk += c
            width += n
    result.append(chunk)
    return '\r\n'.join(result)


def events_from_ics():
    if not SOURCE.exists():
        raise RuntimeError(f'Missing performance calendar: {SOURCE}')
    events, event = [], None
    for line in unfold(SOURCE.read_text(encoding='utf-8-sig')):
        if line == 'BEGIN:VEVENT':
            event = {}
        elif line == 'END:VEVENT':
            if event is not None:
                events.append(event)
            event = None
        elif event is not None and ':' in line:
            key, value = line.split(':', 1)
            event[key] = value
    if len(events) < 100:
        raise RuntimeError(f'Only {len(events)} performances; preserving existing sales calendar')
    return events


def parse_start(event):
    for key, value in event.items():
        if not key.startswith('DTSTART'):
            continue
        if value.endswith('Z'):
            return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(
                tzinfo=timezone.utc).astimezone(TZ)
        if 'VALUE=DATE' in key:
            return datetime.strptime(value, '%Y%m%d').replace(tzinfo=TZ)
        return datetime.strptime(value, '%Y%m%dT%H%M%S').replace(tzinfo=TZ)
    raise ValueError('Missing DTSTART')


def minus_months(day, months):
    year, month0 = divmod(day.year * 12 + day.month - 1 - months, 12)
    month = month0 + 1
    return day.replace(year=year, month=month,
                       day=min(day.day, calendar.monthrange(year, month)[1]))


def sale_for(title, performance_date, bavarian_holidays):
    low = title.casefold()
    if FESTIVAL_START <= performance_date <= FESTIVAL_END:
        return FESTIVAL_SALE, 'Münchner Opernfestspiele 2027: Sonderverkaufsbeginn (bitte offiziell prüfen)'
    if date(2027, 4, 30) <= performance_date <= date(2027, 5, 15) and any(
            key in low for key in JA_MAI_TITLES):
        return JA_MAI_SALE, 'Ja, Mai 2027: Sonderverkaufsbeginn (bitte offiziell prüfen)'
    result = minus_months(performance_date, 2)
    while result.weekday() == 6 or result in bavarian_holidays:
        result -= timedelta(days=1)
    note = 'Regulär: zwei Monate vor der Vorstellung; ggf. vorgezogen bei Sonntag/Feiertag'
    if performance_date.year >= 2028:
        note += '; Sonderfristen künftiger Spielzeiten noch nicht verifiziert'
    return result, note


def utc(dt):
    return dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def event_lines(uid, start, minutes, title, description, stamp):
    lines = [
        'BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{stamp}',
        f'DTSTART:{utc(start)}', f'DTEND:{utc(start + timedelta(minutes=minutes))}',
        f'SUMMARY:{ics_escape(title)}', f'DESCRIPTION:{ics_escape(description)}',
        f'URL:{LINK}',
        # An event visible in the calendar + its own notification at event start.
        'BEGIN:VALARM', 'ACTION:DISPLAY', 'TRIGGER:PT0S',
        f'DESCRIPTION:{ics_escape(title)}', 'END:VALARM', 'END:VEVENT',
    ]
    return lines


def build_calendar(events):
    years = {parse_start(ev).year for ev in events}
    bavarian_holidays = holidays.Germany(subdiv='BY', years=list(range(min(years)-2, max(years)+1)))
    stamp = utc(datetime.now(timezone.utc))
    output = [
        'BEGIN:VCALENDAR', 'VERSION:2.0',
        'PRODID:-//Opera Calendar//Bayerische Staatsoper Vorverkauf//DE',
        'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
        'X-WR-CALNAME:Bayerische Staatsoper – Vorverkauf',
        'X-WR-TIMEZONE:Europe/Berlin', 'X-PUBLISHED-TTL:PT24H',
    ]
    seen = set()
    count_sales = count_reminders = count_special = 0
    for ev in events:
        name = ics_unescape(ev.get('SUMMARY', '')).strip()
        if not name:
            raise RuntimeError('Empty opera title; retaining existing sale calendar')
        performance = parse_start(ev)
        sale_day, rule = sale_for(name, performance.date(), bavarian_holidays)
        sale = datetime.combine(sale_day, time(10, 0), tzinfo=TZ)
        uid_source = ev.get('UID') or f'{name}|{performance.date().isoformat()}'
        base = hashlib.sha256(uid_source.encode('utf-8')).hexdigest()[:30]
        desc = (f'Oper: {name}\nVorstellung: {performance:%d.%m.%Y %H:%M} Uhr (München)'
                f'\nVerkaufsbeginn: {sale:%d.%m.%Y %H:%M} Uhr\n{rule}\n'
                f'Karten: {LINK}\nBitte tatsächlichen Vorverkaufstermin vor dem Kauf überprüfen.')
        sale_uid = f'{base}-sale@munich-opera-sales'
        if sale_uid in seen:
            raise RuntimeError('Duplicate sale UID; retaining existing sale calendar')
        seen.add(sale_uid)
        output += event_lines(sale_uid, sale, 30,
                              f'VVK {name} – {performance:%d.%m.%Y}', desc, stamp)
        count_sales += 1
        if 'Sonderverkaufsbeginn' in rule:
            count_special += 1
        for label, months, days in REMINDERS:
            reminder_day = minus_months(sale_day, months) if months else sale_day - timedelta(days=days)
            reminder = datetime.combine(reminder_day, time(10, 0), tzinfo=TZ)
            suffix = f'{months}m' if months else f'{days}d'
            reminder_uid = f'{base}-reminder-{suffix}@munich-opera-sales'
            if reminder_uid in seen:
                raise RuntimeError('Duplicate reminder UID; retaining existing sale calendar')
            seen.add(reminder_uid)
            output += event_lines(
                reminder_uid, reminder, 10,
                f'VVK-Erinnerung ({label}): {name} – {performance:%d.%m.%Y}',
                f'{desc}\nDies ist die Vorwarnung {label} vor dem Verkaufsbeginn.', stamp)
            count_reminders += 1
    output += ['END:VCALENDAR']
    if count_reminders != 4 * count_sales:
        raise RuntimeError('Invalid reminder count')
    return '\r\n'.join(fold(line) for line in output) + '\r\n', count_sales, count_reminders, count_special


def main():
    content, sales, reminders, specials = build_calendar(events_from_ics())
    # DTSTAMP changes on each run, but stable UIDs prevent duplicate calendar events.
    TARGET.write_bytes(content.encode('utf-8'))
    print(f'Published {sales} sales plus {reminders} separate visible reminders '
          f'({specials} festival-rule events) to {TARGET}')


if __name__ == '__main__':
    main()
