# Bayerische Staatsoper – iCal-Kalender

Zwei abonnierbare Kalender für die Opernvorstellungen und den regulären Vorverkaufsbeginn.

## Installation

1. Neues **öffentliches** GitHub-Repository anlegen, z.B. `bayerische-staatsoper-calendar`.
2. Dateien und den Ordner `.github/workflows/` aus diesem Paket in das Repository hochladen.
3. Unter **Actions** den Workflow `Update Munich opera calendars` auswählen und **Run workflow** ausführen.
4. Unter **Settings → Actions → General → Workflow permissions** gegebenenfalls **Read and write permissions** aktivieren.
5. Nach erfolgreicher Ausführung im Apple Kalender über **Ablage → Neues Kalenderabonnement** die Rohdateien abonnieren:

   `https://raw.githubusercontent.com/USERNAME/bayerische-staatsoper-calendar/main/bayerische-staatsoper.ics`

   `https://raw.githubusercontent.com/USERNAME/bayerische-staatsoper-calendar/main/bayerische-staatsoper-vorverkauf.ics`

   `USERNAME` durch den GitHub-Nutzernamen ersetzen.

## Regeln

- Zeitangaben: München (Europe/Berlin).
- Regulärer Vorverkaufsbeginn: zwei Kalendermonate vor der Vorstellung, 10:00 Uhr.
- Sonntag oder gesetzlicher Feiertag in Bayern: auf den vorhergehenden Werktag vorziehen, gemäss veröffentlichter Regel.
- Verkaufsstart enthält vier Kalenderalarme: 3 Monate, 1 Monat, 1 Woche und 1 Tag vorher.
- Bei unbekannter Aufführungsdauer wird **keine** Dauer geschätzt: ein einminütiger Startmarker ist gesetzt.
- Leere oder unerwartet kleine Abfragen lösen einen Fehler aus, statt bestehende Kalender zu überschreiben.

## Wichtiger Testhinweis

Die aktuelle Website-Navigation konnte in dieser Umgebung nicht live mit Playwright getestet werden. Der erste GitHub-Actions-Lauf ist daher ein **Integrationstest**; vor dem Abonnieren bitte die Zahl der Termine und mehrere Operndaten mit dem offiziellen Spielplan vergleichen. Die Monatsnavigation oder CSS-Selektoren könnten Anpassungen benötigen. Der Zugriff auf alle Spielplanmonate ist damit noch **nicht verifiziert**.

Quelle: https://www.staatsoper.de/spielplan und https://www.staatsoper.de/kartenkauf
