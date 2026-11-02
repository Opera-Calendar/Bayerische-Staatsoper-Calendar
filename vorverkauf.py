#!/usr/bin/env python3
"""Generate Munich ticket-sale subscription from the existing performance ICS.
Official rules: https://www.staatsoper.de/kartenkauf/
No additional scraping or changes to the performance calendar.
"""
from __future__ import annotations
import hashlib
import calendar
from datetime import datetime, date, time, timedelta, timezone
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


def unfold(text):
    rows = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line.startswith((' ', '\t')) and rows:
            rows[-1] += line[1:]
        else:
            rows.append(line)
    return rows


def decode_ics(value):
    return (value.replace('\\n', '\n').replace('\\N', '\n')
            .replace('\\,', ',').replace('\\;', ';').replace('\\\\', '\\'))


def escape(value):
    return (str(value).replace('\\', '\\\\').replace('\n', '\\n')
            .replace(',', '\\,').replace(';', '\\;'))


def fold(line):
    lines, current, width = [], '', 0
    for c in line:
        n = len(c.encode('utf-8'))
        if current and width + n > 75:
            lines.append(current)
            current, width = ' ' + c, n + 1
        else:
            current += c
            width += n
    lines.append(current)
    return '\r\n'.join(lines)


def events_from_ics():
    if not SOURCE.exists():
        raise RuntimeError(f'Missing performance calendar: {SOURCE}')
    text = SOURCE.read_text(encoding='utf-8-sig')
    output, event = [], None
    for line in unfold(text):
        if line == 'BEGIN:VEVENT':
            event = {}
        elif line == 'END:VEVENT':
            if event:
                output.append(event)
            event = None
        elif event is not None and ':' in line:
            key, value = line.split(':', 1)
            event[key] = value
    if len(output) < 100:
        raise RuntimeError(f'Only {len(output)} performance events; preserving prior sales calendar')
    return output


def parse_start(event):
    for key in event:
        if key.startswith('DTSTART'):
            value = event[key]
            if value.endswith('Z'):
                return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).astimezone(TZ)
            if 'TZID=' in key:
                return datetime.strptime(value, '%Y%m%dT%H%M%S').replace(tzinfo=TZ)
            if 'VALUE=DATE' in key:
                return datetime.strptime(value, '%Y%m%d').replace(tzinfo=TZ)
            return datetime.strptime(value, '%Y%m%dT%H%M%S').replace(tzinfo=TZ)
    raise ValueError('Missing DTSTART')


def minus_months(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def sale_for(title, day, german_holidays):
    lower = title.casefold()
    if FESTIVAL_START <= day <= FESTIVAL_END:
        return FESTIVAL_SALE, 'Münchner Opernfestspiele 2027: Sonderverkaufsbeginn (offiziell)'
    if date(2027, 4, 30) <= day <= date(2027, 5, 15) and any(w in lower for w in JA_MAI_TITLES):
        return JA_MAI_SALE, 'Ja, Mai 2027: Sonderverkaufsbeginn (offiziell)'
    result = minus_months(day, 2)
    while result.weekday() == 6 or result in german_holidays:
        result -= timedelta(days=1)
    note = 'Regulärer Verkaufsbeginn: zwei Monate vorher, ggf. vorgezogen bei Sonntag/Feiertag'
    if day.year >= 2028:
        note += '; mögliche Sondersaison-Fristen bitte offiziell prüfen'
    return result, note


def utc(dt):
    return dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def main():
    events = events_from_ics()
    years = {parse_start(ev).year for ev in events}
    bavaria_holidays = holidays.Germany(subdiv='BY', years=list(range(min(years)-1, max(years)+1)))
    stamp = utc(datetime.now(timezone.utc))
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Opera Calendar//Bayerische Staatsoper Vorverkauf//DE',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:Bayerische Staatsoper – Vorverkauf',
             'X-WR-TIMEZONE:Europe/Berlin', 'X-PUBLISHED-TTL:PT24H']
    count, special = 0, 0
    for ev in events:
        name = decode_ics(ev.get('SUMMARY', '')).strip()
        if not name:
            raise RuntimeError('Empty performance title; refusing to replace existing calendar')
        performance = parse_start(ev)
        sale_day, rule = sale_for(name, performance.date(), bavaria_holidays)
        sale = datetime.combine(sale_day, time(10, 0), tzinfo=TZ)
        uid_source = ev.get('UID') or f'{name}|{performance.isoformat()}'
        uid = hashlib.sha256(uid_source.encode()).hexdigest()[:30] + '@munich-opera-sales'
        description = (f'Vorverkauf für {name} am {performance:%d.%m.%Y, %H:%M} Uhr (München).\n'
                       f'{rule}.\nKartenverkauf: {LINK}\n'
                       'Bitte Termin und Sonderregelungen vor dem Kauf auf der offiziellen Website prüfen.')
        lines += ['BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{stamp}',
                  f'DTSTART:{utc(sale)}', f'DTEND:{utc(sale + timedelta(minutes=30))}',
                  f'SUMMARY:{escape("VVK " + name + " – " + performance.strftime("%d.%m.%Y"))}',
                  f'DESCRIPTION:{escape(description)}', f'URL:{LINK}']
        for alarm in (sale.replace(year=minus_months(sale.date(), 3).year, month=minus_months(sale.date(), 3).month, day=minus_months(sale.date(), 3).day),
                      sale.replace(year=minus_months(sale.date(), 1).year, month=minus_months(sale.date(), 1).month, day=minus_months(sale.date(), 1).day),
                      sale - timedelta(weeks=1), sale - timedelta(days=1)):
            lines += ['BEGIN:VALARM', 'ACTION:DISPLAY',
                      f'DESCRIPTION:{escape("Vorverkauf: " + name)}',
                      f'TRIGGER;VALUE=DATE-TIME:{utc(alarm)}', 'END:VALARM']
        lines.append('END:VEVENT')
        count += 1
        if 'Sonderverkaufsbeginn' in rule:
            special += 1
    lines.append('END:VCALENDAR')
    contents = '\r\n'.join(map(fold, lines)) + '\r\n'
    if TARGET.exists() and TARGET.read_bytes() == contents.encode('utf-8'):
        print(f'No changes: {count} sales events ({special} festival exceptions)')
        return
    TARGET.write_bytes(contents.encode('utf-8'))
    print(f'Published {count} sale reminders ({special} festival exceptions) to {TARGET}')


if __name__ == '__main__':
    main()
