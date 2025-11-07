# Claude Desktop Integration

Diese Anleitung zeigt, wie du den httpcatcher MCP Server in Claude Desktop integrierst.

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
4. **Session Files importiert** (optional):
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

## Verfügbare Tools

Nach der Integration stehen folgende Tools zur Verfügung:

### 1. `search_requests`
Suche HTTP Requests mit flexiblen Filtern:
- Method, URL, Host, Path
- Status codes, Content-Type
- Headers, Cookies
- Body-Inhalte
- Zeiträume

**Beispiel:**
> "Suche alle POST Requests an example.com mit Status 200"

### 2. `get_request_details`
Hole detaillierte Informationen zu einem Request:
- Vollständige Headers
- Request/Response Bodies
- Timing-Informationen
- Optional: Body-Dekompression

**Beispiel:**
> "Zeige mir Details zu Request ID 123"

### 3. `get_stats`
Erhalte Statistiken über alle Requests:
- Verteilung nach Method
- Status Code Verteilung
- Top Hosts
- Content-Type Kategorien
- Durchschnittliche Response-Zeiten

**Beispiel:**
> "Zeige mir Statistiken über alle Requests"

### 4. `list_files`
Liste alle indizierten Session Files:
- Dateiname
- Anzahl Requests
- Dateigröße
- Indexierungs-Zeitpunkt

**Beispiel:**
> "Liste alle Session Files"

### 5. `get_available_keys`
Zeige alle verfügbaren Header oder Cookie Keys:
- Mit Nutzungs-Zählern
- Sortiert nach Häufigkeit

**Beispiel:**
> "Welche Header Keys gibt es?"

### 6. `autocomplete_key`
Autovervollständigung für Key-Namen:
- Prefix-basierte Suche
- Für Headers oder Cookies

**Beispiel:**
> "Welche Header beginnen mit 'Content'?"

### 7. `index_file`
Indexiere eine neue Session File:
- Fügt File zur Datenbank hinzu
- Parsed alle Requests
- Erstellt Indices

**Beispiel:**
> "Indexiere die Datei /path/to/session.hcs"

## Verwendungsbeispiele

### Analyse von API Calls

> "Suche alle API Calls an api.github.com in den letzten 30 Tagen und zeige mir die häufigsten Endpunkte"

### Fehleranalyse

> "Finde alle Requests mit Status 4xx oder 5xx und gruppiere nach Error-Type"

### Performance-Analyse

> "Zeige mir die langsamsten Requests (>1000ms) und analysiere deren Response-Times"

### Cookie/Header Analyse

> "Welche Authorization Headers werden verwendet und wie oft?"

### Content-Type Analyse

> "Liste alle JSON API Calls auf und zeige mir deren Request/Response Strukturen"

## Troubleshooting

### Server startet nicht

1. **Prüfe die Logs:**
   ```bash
   # Linux/macOS
   tail -f ~/.config/Claude/logs/mcp*.log

   # Windows
   type %APPDATA%\Claude\logs\mcp*.log
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

### Langsame Queries

Bei großen Datenbanken (>10.000 Requests):
1. **Vacuum ausführen:**
   ```bash
   hc-mcp db vacuum
   ```
2. **Filter verwenden** um Ergebnisse zu begrenzen
3. **Limit-Parameter** bei Suchen nutzen

## Async Performance

Der Server nutzt vollständig async/await für optimale Performance:
- ✅ Keine Threading-Locks
- ✅ Effiziente I/O Operationen
- ✅ Parallele Query-Verarbeitung
- ✅ Keine SQLite Threading-Errors

Alle 7 Tools wurden mit async Database-Connections getestet und funktionieren einwandfrei!

## Support

Bei Problemen:
1. Prüfe die Logs in `~/.config/Claude/logs/`
2. Teste die CLI-Befehle: `hc-mcp db status`
3. Führe Tests aus: `python test_mcp_server.py`

## Nächste Schritte

Nach erfolgreicher Integration:
1. Importiere deine Session Files: `hc-mcp files add-dir`
2. Teste die Suche in Claude Desktop
3. Nutze natürliche Sprache für komplexe Analysen!
