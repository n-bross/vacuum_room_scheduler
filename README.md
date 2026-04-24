# Vacuum Room Scheduler

Home Assistant custom integration (`vacuum_room_scheduler`) that tracks room cleaning age, only starts cleaning when nobody is home, and uses TTS prompts for overdue rooms.

## Features (English)

- Tracks last vacuumed and last mopped timestamps per room (persistent storage across restarts)
- Uses room + segment mapping (e.g. Kitchen -> segment 16)
- Can auto-discover rooms from Home Assistant and filter to the same floor as the vacuum
- Checks a presence `binary_sensor` before any vacuum start
- Automatically cleans overdue room tasks (vacuum/mop) only when nobody is home
- If people are home and a task is overdue, sends a TTS prompt with response options
- Handles responses like:
  - `jetzt` / `now`
  - `in 2 Stunden` / `in 2 hours`
  - `in 3 Tagen` / `in 3 days`
- Sends a reminder TTS about 10 minutes before a scheduled clean
- Supports preferred cleaning time window (for example 09:00-17:00)

## Installation via HACS (English)

1. Open HACS in Home Assistant.
2. Go to `Integrations`.
3. Open menu (`⋮`) -> `Custom repositories`.
4. Add this repository URL and select category `Integration`.
5. Search for `Vacuum Room Scheduler` in HACS and install it.
6. Restart Home Assistant.

## Setup (English)

1. Go to `Settings` -> `Devices & Services` -> `Add Integration`.
2. Search for `Vacuum Room Scheduler`.
3. Fill in:
   - Vacuum entity (domain `vacuum`)
   - Presence sensor (domain `binary_sensor`)
   - TTS service (for example `tts.google_translate_say`)
   - Media player entity (domain `media_player`)
   - Maximum days without cleaning per task type (vacuum/mop, default `7`)
   - Preferred window start/end (for example `09:00` to `17:00`)
4. Add rooms dynamically in the flow:
   - `Add room` -> room name + segment ID
   - `Discover rooms from Home Assistant` to import rooms on the same floor as the vacuum
   - `Remove room` if needed
   - `Finish setup`

## Response Handling (English)

The integration supports two ways:

1. `input_select` updates: when an `input_select` changes to phrases like `jetzt`, `in 2 Stunden`, `in 3 Tagen`, the integration parses the value.
2. Service/event based conversation wiring:
   - Call service `vacuum_room_scheduler.handle_response`
   - Or fire event `vacuum_room_scheduler_response`
   - Optional `mode`: `vacuum`/`saugen` or `mop`/`wischen`

Service example:

```yaml
service: vacuum_room_scheduler.handle_response
data:
  response: "in 2 Stunden"
  room: "Kitchen"
  mode: "mop"
```

Event example:

```yaml
event_type: vacuum_room_scheduler_response
event_data:
  response: "jetzt"
  room: "Living Room"
  mode: "vacuum"
```

---

## Funktionen (Deutsch)

- Speichert pro Raum getrennte Zeitpunkte für letztes Saugen und letztes Wischen (persistent über Neustarts)
- Nutzt Raum-zu-Segment-Zuordnung (z. B. Küche -> Segment 16)
- Kann Räume automatisch aus Home Assistant erkennen und auf den gleichen Stock wie den Staubsauger filtern
- Prüft vor jedem Start einen Anwesenheits-`binary_sensor`
- Reinigt überfällige Aufgaben (Saugen/Wischen) nur, wenn niemand zu Hause ist
- Wenn jemand zu Hause ist und eine Aufgabe überfällig ist, erfolgt eine TTS-Nachfrage
- Verarbeitet Antworten wie:
  - `jetzt`
  - `in 2 Stunden`
  - `in 3 Tagen`
- Sendet etwa 10 Minuten vor geplanter Reinigung eine TTS-Erinnerung
- Unterstützt ein bevorzugtes Zeitfenster (z. B. 09:00-17:00)

## Installation über HACS (Deutsch)

1. HACS in Home Assistant öffnen.
2. Zu `Integrations` gehen.
3. Menü (`⋮`) -> `Custom repositories`.
4. Repository-URL hinzufügen und Kategorie `Integration` wählen.
5. `Vacuum Room Scheduler` in HACS suchen und installieren.
6. Home Assistant neu starten.

## Einrichtung (Deutsch)

1. `Einstellungen` -> `Geräte & Dienste` -> `Integration hinzufügen`.
2. `Vacuum Room Scheduler` auswählen.
3. Folgende Werte setzen:
   - Staubsauger-Entität (Domain `vacuum`)
   - Anwesenheitssensor (Domain `binary_sensor`)
   - TTS-Service (z. B. `tts.google_translate_say`)
   - Media-Player-Entität (Domain `media_player`)
   - Maximale Tage ohne Reinigung pro Aufgabentyp (Saugen/Wischen, Standard `7`)
   - Bevorzugtes Zeitfenster Start/Ende (z. B. `09:00` bis `17:00`)
4. Räume dynamisch im Assistenten verwalten:
   - `Raum hinzufügen` -> Raumname + Segment-ID
   - `Räume aus Home Assistant erkennen` für Räume auf demselben Stock wie der Staubsauger
   - `Raum entfernen` bei Bedarf
   - `Einrichtung abschließen`

## Antworten verarbeiten (Deutsch)

Zwei Wege sind unterstützt:

1. `input_select`: Wenn ein `input_select` auf Werte wie `jetzt`, `in 2 Stunden`, `in 3 Tagen` wechselt, wird die Antwort geparst.
2. Service/Event für Assist- oder Intent-Automationen:
   - Service `vacuum_room_scheduler.handle_response`
   - Event `vacuum_room_scheduler_response`
   - Optionales Feld `mode`: `vacuum`/`saugen` oder `mop`/`wischen`

Service-Beispiel:

```yaml
service: vacuum_room_scheduler.handle_response
data:
  response: "in 3 Tagen"
  room: "Küche"
  mode: "wischen"
```

Event-Beispiel:

```yaml
event_type: vacuum_room_scheduler_response
event_data:
  response: "jetzt"
  room: "Wohnzimmer"
  mode: "saugen"
```

## Notes

- Segment commands differ by vacuum model/integration. This integration tries `clean_segment` with common parameter formats and falls back to `vacuum.start`.
- The scheduler tracks vacuuming and mopping separately. Start command execution still uses the configured vacuum integration command.
- Preferred window applies to automatic and scheduled starts.
