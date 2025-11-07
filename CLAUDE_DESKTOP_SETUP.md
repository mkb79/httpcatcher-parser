# Claude Desktop Integration

Diese Anleitung zeigt, wie du den httpcatcher MCP Server in Claude Desktop integrierst und alle verfügbaren Tools nutzt.

## Voraussetzungen

1. **Claude Desktop** installiert
2. **Projekt installiert** mit MCP dependencies:
   ```bash
   uv pip install -e ".[mcp]"
   ```
3. **Datenbank initialisiert**:
   ```bash
   hc-mcp init
   ```
4. **Session Files importiert**:
   ```bash
   hc-mcp files add-dir /path/to/hc_sessions/ --copy
   ```

## Konfiguration

### 1. Konfigurationsdatei finden

Die Claude Desktop Konfiguration liegt hier:

**Linux:**
```bash
~/.config/Claude/claude_desktop_config.json
```

**macOS:**
```bash
~/Library/Application Support/Claude/claude_desktop_config.json
```

**Windows:**
```
%APPDATA%\Claude\claude_desktop_config.json
```

### 2. MCP Server hinzufügen

Öffne die `claude_desktop_config.json` und füge den httpcatcher Server hinzu:

```json
{
  "mcpServers": {
    "httpcatcher": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/httpcatcher-parser",
        "run",
        "--extra",
        "mcp",
        "hc-mcp",
        "server"
      ]
    }
  }
}
```

**Wichtig:** Ersetze `/absolute/path/to/httpcatcher-parser` mit dem **absoluten Pfad** zu deinem Projekt!

Beispiel:
- Linux/macOS: `/home/username/projects/httpcatcher-parser`
- Windows: `C:\\Users\\username\\projects\\httpcatcher-parser`

### 3. Claude Desktop neu starten

Nach der Konfiguration:
1. Claude Desktop komplett beenden
2. Claude Desktop neu starten
3. Der Server sollte nun im MCP Panel sichtbar sein

---

## 📚 Verfügbare Tools - Detaillierte Referenz

### 1. `search_requests` - HTTP Requests durchsuchen

**Zweck**: Durchsuche alle indizierten HTTP Requests mit flexiblen Filtern.

#### Parameter

| Parameter | Typ | Beschreibung | Beispiel |
|-----------|-----|--------------|----------|
| `method` | string | HTTP Method (GET, POST, etc.) | `"POST"` |
| `url` | string | URL-Pattern (partial match) | `"api.example.com"` |
| `host` | string | Host-Pattern (partial match) | `"example.com"` |
| `path` | string | Pfad-Pattern (partial match) | `"/auth/register"` |
| `status_codes` | array[int] | Spezifische Status Codes | `[200, 201]` |
| `status_min` | int | Minimaler Status Code | `200` |
| `status_max` | int | Maximaler Status Code | `299` |
| `req_content_type` | string | Request Content-Type | `"application/json"` |
| `resp_content_type` | string | Response Content-Type | `"application/json"` |
| `resp_content_category` | enum | Content Kategorie | `"json"`, `"image"`, `"html"` |
| `body_search` | string | Text-Suche in Request/Response Bodies | `"metadata1"` |
| `body_in_request` | bool | Suche in Request Bodies | `true` |
| `body_in_response` | bool | Suche in Response Bodies | `true` |
| `time_start` | int | Start-Timestamp (Unix ms) | `1640995200000` |
| `time_end` | int | End-Timestamp (Unix ms) | `1672531199000` |
| `headers` | array | Header-Filter (siehe unten) | `[{"key": "Authorization"}]` |
| `cookies` | array | Cookie-Filter (siehe unten) | `[{"key": "session"}]` |
| `file_ids` | array[int] | Nur bestimmte Files durchsuchen | `[1, 2, 3]` |
| `limit` | int | Max. Anzahl Ergebnisse (default: 100) | `50` |
| `offset` | int | Überspringe N Ergebnisse | `0` |
| `sort_by` | enum | Sortierung | `"req_timestamp"`, `"duration_ms"`, `"status_code"` |
| `sort_desc` | bool | Absteigend sortieren | `true` |

#### Header-Filter Format
```json
{
  "key": "Authorization",        // Header-Name (optional)
  "value": "Bearer",             // Header-Wert (optional, partial match)
  "request": true                // true = Request Headers, false = Response Headers
}
```

#### Cookie-Filter Format
```json
{
  "key": "session",              // Cookie-Name (optional)
  "value": "abc123",             // Cookie-Wert (optional, partial match)
  "request": true                // true = Request Cookies, false = Response Cookies
}
```

#### Beispiele

**Einfache Suche:**
> "Suche alle POST Requests an api.example.com"

**Mit Status Filter:**
> "Suche alle Requests mit Status 4xx oder 5xx"
> (Parameter: `status_min: 400`, `status_max: 599`)

**Body-Suche:**
> "Suche alle Requests die 'metadata1' im Body enthalten"
> (Parameter: `body_search: "metadata1"`)

**Komplexe Query:**
> "Suche alle POST Requests an /auth/register mit Status 200-299 die 'metadata1' im Request Body haben, sortiert nach Datum"

Parameter:
```json
{
  "path": "/auth/register",
  "method": "POST",
  "status_min": 200,
  "status_max": 299,
  "body_search": "metadata1",
  "body_in_request": true,
  "body_in_response": false,
  "sort_by": "req_timestamp",
  "sort_desc": true,
  "limit": 50
}
```

**Zeitraum-Filter:**
> "Suche alle Requests von Januar 2024"
> (Parameter: `time_start: 1704067200000`, `time_end: 1706745599000`)

**Header-Filter:**
> "Suche alle Requests mit Authorization Header"

Parameter:
```json
{
  "headers": [
    {
      "key": "Authorization",
      "request": true
    }
  ]
}
```

#### Wichtig: Body-Suche vs. Details

⚠️ **`search_requests` zeigt KEINE vollständigen Bodies!**

Die Suche findet Requests basierend auf Body-Inhalten, gibt aber nur:
- Metadaten (ID, Host, Path, Status, etc.)
- KEINE vollständigen Request/Response Bodies

Um Bodies zu sehen:
1. Hole zuerst die Request-IDs mit `search_requests`
2. Dann nutze `get_request_details` für jeden Request

**Beispiel-Workflow:**
```
1. Suche: "Finde alle POST Requests mit 'metadata1' im Body"
   → Gibt IDs zurück: [123, 456, 789]

2. Details: "Zeige mir die vollständigen Details von Request 123"
   → Gibt vollständige Request/Response Bodies zurück
```

---

### 2. `get_request_details` - Vollständige Request-Details

**Zweck**: Hole vollständige Informationen zu einem spezifischen Request einschließlich Bodies.

#### Parameter

| Parameter | Typ | Beschreibung | Beispiel |
|-----------|-----|--------------|----------|
| `request_id` | int | **Pflicht**: Die Request-ID aus search_requests | `123` |
| `detail_level` | enum | Detailgrad | `"full"` (default) |
| `decompress` | bool | Bodies dekomprimieren (gzip/brotli) | `false` (default) |

#### Detail Levels

| Level | Was wird zurückgegeben |
|-------|------------------------|
| `full` | Alles: Metadata, Headers, Request Body, Response Body |
| `headers_only` | Nur Metadata und alle Headers (keine Bodies) |
| `request_only` | Nur Request-Seite (Headers + Body) |
| `response_only` | Nur Response-Seite (Headers + Body) |
| `metadata` | Nur Basis-Metadaten (keine Headers, keine Bodies) |

#### Body-Encoding

Bodies werden als **base64** zurückgegeben wenn sie binär sind:
```json
{
  "request_body": {
    "_type": "base64",
    "data": "eyJmb28iOiAiYmFyIn0="
  }
}
```

Text-Bodies werden direkt zurückgegeben:
```json
{
  "request_body": "{\"foo\": \"bar\"}"
}
```

#### Beispiele

**Vollständige Details:**
> "Zeige mir alle Details von Request 123"

**Nur Request Body:**
> "Zeige mir nur den Request Body von Request 123"
> (Parameter: `detail_level: "request_only"`)

**Mit Dekompression:**
> "Zeige mir Request 456 und dekomprimiere die Bodies"
> (Parameter: `decompress: true`)

**Nur Headers:**
> "Zeige mir alle Headers von Request 789"
> (Parameter: `detail_level: "headers_only"`)

#### Typischer Workflow

1. **Suche** nach interessanten Requests:
   ```
   "Finde alle POST Requests an /api/auth"
   ```

2. **Hole IDs** aus den Ergebnissen (z.B. 123, 456)

3. **Details abrufen**:
   ```
   "Zeige mir die vollständigen Details von Request 123"
   ```

4. **Analyse** der Bodies, Headers, etc.

---

### 3. `get_stats` - Statistiken über Requests

**Zweck**: Erhalte aggregierte Statistiken über alle oder bestimmte Requests.

#### Parameter

| Parameter | Typ | Beschreibung | Beispiel |
|-----------|-----|--------------|----------|
| `file_id` | int | Optional: Nur Stats für ein bestimmtes File | `5` |

#### Zurückgegebene Statistiken

- **Total Requests**: Gesamtanzahl
- **By Method**: Verteilung nach HTTP Method (GET, POST, etc.)
- **By Status**: Verteilung nach Status Code
- **By Content Category**: JSON, HTML, Images, etc.
- **Top Hosts**: Meistbesuchte Hosts
- **Time Range**: Ältester und neuester Request
- **Duration Stats**: Avg/Min/Max Response-Zeiten
- **Body Sizes**: Durchschnittliche Request/Response Größen

#### Beispiele

**Gesamt-Statistiken:**
> "Zeige mir Statistiken über alle Requests"

**File-spezifisch:**
> "Zeige mir Statistiken nur für File 5"
> (Parameter: `file_id: 5`)

**Analyse-Fragen:**
> "Welche HTTP Methods werden am häufigsten verwendet?"
> "Was sind die langsamsten Endpunkte?"
> "Welche Hosts werden am meisten angesprochen?"

---

### 4. `list_files` - Indexierte Session Files

**Zweck**: Liste alle indizierten Session Files mit Metadaten.

#### Parameter

| Parameter | Typ | Beschreibung | Beispiel |
|-----------|-----|--------------|----------|
| `sort_by` | enum | Sortierung | `"date"` (default), `"name"`, `"size"`, `"requests"` |

#### Zurückgegebene Daten pro File

- **ID**: File-ID für Filterung
- **Filename**: Dateiname
- **File Path**: Vollständiger Pfad
- **File Size**: Größe in Bytes
- **Request Count**: Anzahl indexierter Requests
- **Indexed At**: Zeitpunkt der Indexierung

#### Beispiele

**Alle Files:**
> "Liste alle indizierten Session Files"

**Sortiert nach Größe:**
> "Zeige mir die größten Session Files"
> (Parameter: `sort_by: "size"`)

**Sortiert nach Requests:**
> "Welches File hat die meisten Requests?"
> (Parameter: `sort_by: "requests"`)

---

### 5. `get_available_keys` - Verfügbare Header/Cookie Keys

**Zweck**: Zeige alle Header- oder Cookie-Namen die in den Requests vorkommen.

#### Parameter

| Parameter | Typ | Beschreibung | Beispiel |
|-----------|-----|--------------|----------|
| `key_type` | enum | **Pflicht**: Typ der Keys | `"header"` oder `"cookie"` |

#### Zurückgegebene Daten

Array von Objekten:
```json
[
  {
    "name": "Content-Type",
    "name_lower": "content-type",
    "usage_count": 1523
  }
]
```

Sortiert nach Usage Count (häufigste zuerst).

#### Beispiele

**Alle Header Keys:**
> "Welche HTTP Header werden verwendet?"
> (Parameter: `key_type: "header"`)

**Alle Cookie Keys:**
> "Welche Cookies werden gesetzt?"
> (Parameter: `key_type: "cookie"`)

**Analyse:**
> "Welcher Header wird am häufigsten verwendet?"
> "Gibt es ungewöhnliche oder seltene Headers?"

---

### 6. `autocomplete_key` - Key-Namen vervollständigen

**Zweck**: Finde Header- oder Cookie-Namen die mit einem Prefix beginnen.

#### Parameter

| Parameter | Typ | Beschreibung | Beispiel |
|-----------|-----|--------------|----------|
| `prefix` | string | **Pflicht**: Suchprefix | `"Content"` |
| `key_type` | enum | **Pflicht**: Typ der Keys | `"header"` oder `"cookie"` |
| `limit` | int | Max. Anzahl Ergebnisse (default: 20) | `10` |

#### Zurückgegebene Daten

Array von Key-Namen:
```json
["Content-Type", "Content-Length", "Content-Encoding"]
```

#### Beispiele

**Header-Suche:**
> "Welche Header beginnen mit 'Content'?"
> (Parameter: `prefix: "Content"`, `key_type: "header"`)

**Cookie-Suche:**
> "Welche Cookies beginnen mit 'session'?"
> (Parameter: `prefix: "session"`, `key_type: "cookie"`)

**Exploration:**
> "Welche 'Auth'-Header gibt es?"
> (Parameter: `prefix: "Auth"`, `key_type: "header"`)

---

### 7. `index_file` - Neue Session File indexieren

**Zweck**: Füge eine neue Session File zur Datenbank hinzu.

#### Parameter

| Parameter | Typ | Beschreibung | Beispiel |
|-----------|-----|--------------|----------|
| `file_path` | string | **Pflicht**: Pfad zur .hcs Datei | `"/path/to/session.hcs"` |
| `force_reindex` | bool | Neu indexieren falls bereits vorhanden | `false` (default) |

#### Rückgabewert

```json
{
  "file_id": 42,
  "requests_added": 156,
  "file_path": "/path/to/session.hcs"
}
```

#### Beispiele

**Neue Datei:**
> "Indexiere die Datei /path/to/new_session.hcs"

**Neu-Indexierung:**
> "Indexiere /path/to/session.hcs neu"
> (Parameter: `force_reindex: true`)

---

## 🎯 Typische Workflows

### Workflow 1: Request-Analyse

1. **Übersicht** verschaffen:
   ```
   "Zeige mir Statistiken über alle Requests"
   ```

2. **Nach interessanten Requests suchen**:
   ```
   "Suche alle POST Requests an /api/auth mit Status 200-299"
   ```

3. **Details ansehen**:
   ```
   "Zeige mir die vollständigen Details von Request 123"
   ```

4. **Bodies analysieren**:
   ```
   "Was steht im Request Body von Request 123?"
   ```

### Workflow 2: API-Endpoint Analyse

1. **Alle Endpunkte finden**:
   ```
   "Welche Endpunkte werden auf api.example.com angesprochen?"
   ```

2. **Spezifischen Endpunkt untersuchen**:
   ```
   "Suche alle Requests an /api/users"
   ```

3. **Erfolgsrate prüfen**:
   ```
   "Wie viele davon waren erfolgreich (Status 200)?"
   ```

4. **Fehler analysieren**:
   ```
   "Zeige mir die fehlgeschlagenen Requests (Status 4xx oder 5xx)"
   ```

### Workflow 3: Header/Cookie Analyse

1. **Übersicht**:
   ```
   "Welche HTTP Headers werden verwendet?"
   ```

2. **Spezifischen Header suchen**:
   ```
   "Suche alle Requests mit Authorization Header"
   ```

3. **Header-Werte analysieren**:
   ```
   "Zeige mir Request 123 und analysiere die Authorization Header"
   ```

### Workflow 4: Zeitliche Analyse

1. **Zeitraum definieren**:
   ```
   "Suche alle Requests vom Januar 2024"
   ```

2. **Trends erkennen**:
   ```
   "Gab es Änderungen in den Request-Strukturen über die Zeit?"
   ```

3. **Vergleich**:
   ```
   "Vergleiche Requests von 2023 mit 2024"
   ```

---

## 💡 Tipps & Best Practices

### 1. Body-Suche effektiv nutzen

✅ **Richtig:**
```
1. "Suche Requests mit 'metadata1' im Body"
2. "Zeige mir die vollständigen Details der gefundenen Requests"
```

❌ **Falsch:**
```
"Zeige mir den Body von Requests mit 'metadata1'"
→ search_requests zeigt keine Bodies!
```

### 2. Pagination bei vielen Ergebnissen

Bei > 100 Ergebnissen nutze `limit` und `offset`:
```
"Suche die ersten 50 POST Requests"
→ limit: 50, offset: 0

"Suche die nächsten 50 POST Requests"
→ limit: 50, offset: 50
```

### 3. Status Code Filter kombinieren

Für Bereiche nutze `status_min` und `status_max`:
```
"Alle erfolgreichen Requests" → status_min: 200, status_max: 299
"Alle Client-Fehler" → status_min: 400, status_max: 499
"Alle Server-Fehler" → status_min: 500, status_max: 599
```

### 4. Host vs. URL vs. Path

- **host**: Nur der Hostname (z.B. `"api.example.com"`)
- **path**: Nur der Pfad (z.B. `"/auth/register"`)
- **url**: Vollständige URL (z.B. `"https://api.example.com/auth"`)

Verwende `host` + `path` für präzisere Suchen!

### 5. Content-Type Kategorien

Nutze `resp_content_category` statt `resp_content_type`:
- `"json"` - Alle JSON Responses
- `"html"` - Alle HTML Responses
- `"image"` - Alle Images (jpeg, png, gif, etc.)
- `"media"` - Videos, Audio
- `"xml"` - XML/SOAP
- etc.

### 6. File-basierte Analyse

Wenn du nur ein bestimmtes Session File analysieren willst:
```
1. "Liste alle Files" → Finde File-ID
2. "Zeige mir Stats für File 5"
3. "Suche Requests nur in File 5" → file_ids: [5]
```

---

## 🐛 Troubleshooting

### Server startet nicht

1. **Prüfe die Logs:**
   ```bash
   # macOS
   tail -f ~/Library/Application\ Support/Claude/logs/mcp*.log

   # Linux
   tail -f ~/.config/Claude/logs/mcp*.log
   ```

2. **Teste den Server manuell:**
   ```bash
   cd /path/to/httpcatcher-parser
   uv run --extra mcp hc-mcp server
   ```

3. **Prüfe Dependencies:**
   ```bash
   uv pip list | grep -E "(aiosqlite|aiofiles|mcp)"
   ```

### Tools erscheinen nicht

1. **Prüfe den Pfad** in der Konfiguration (muss absolut sein!)
2. **Prüfe ob Datenbank existiert:**
   ```bash
   ls -la ~/.http_catcher/index.db
   ```
3. **Initialisiere falls nötig:**
   ```bash
   hc-mcp init
   ```

### Keine Ergebnisse bei Body-Suche

⚠️ **Wichtig**: `body_search` durchsucht nur die in der DB gespeicherten Bodies!

- Sehr große Bodies (>1MB) werden eventuell nicht vollständig indexiert
- Binäre Bodies werden nicht durchsucht
- Nur Text-Inhalte sind durchsuchbar

### Langsame Queries

Bei großen Datenbanken (>10,000 Requests):

1. **Vacuum ausführen:**
   ```bash
   hc-mcp db vacuum
   ```

2. **Filter verwenden** um Ergebnisse zu begrenzen:
   - Zeitraum eingrenzen (`time_start`, `time_end`)
   - Host/Path Filter nutzen
   - Limit reduzieren

3. **File-basiert arbeiten** statt über alle Files

---

## 📊 Performance

Der Server nutzt vollständig async/await für optimale Performance:

- ✅ Keine Threading-Locks
- ✅ Effiziente I/O Operationen
- ✅ Parallele Query-Verarbeitung möglich
- ✅ Keine SQLite Threading-Errors
- ✅ Alle 7 Tools getestet und optimiert

Typische Query-Zeiten:
- `search_requests`: 100-500ms (je nach Filter)
- `get_request_details`: 10-50ms pro Request
- `get_stats`: 200-800ms (abhängig von Datenmenge)
- `list_files`: 10-50ms

---

## 🔒 Datenschutz

⚠️ **Wichtig**: Session Files enthalten potentiell sensible Daten!

- `.hcs` Files werden **nicht** ins Git-Repository committed (siehe `.gitignore`)
- Analyse-Outputs werden **nicht** committed
- Die Datenbank (`~/.http_catcher/`) bleibt lokal
- MCP Server läuft nur lokal, keine Cloud-Verbindung

Sei vorsichtig beim Teilen von:
- Request/Response Bodies (können Credentials, Tokens, etc. enthalten)
- Headers (Authorization, Cookies)
- URLs (können API Keys enthalten)

---

## 📚 Weitere Ressourcen

- **CLI Dokumentation**: Führe `hc-mcp --help` aus
- **Async Implementation**: Siehe `ASYNC_IMPLEMENTATION.md`
- **GitHub Issues**: https://github.com/mkb79/httpcatcher-parser/issues

Bei Fragen oder Problemen öffne ein Issue auf GitHub!
