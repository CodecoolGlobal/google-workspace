# Google Workspace MCP szerver

Ez a `google-workspace` skill MCP-szerver változata. A lényeg: **nem kell többé
mountolni a `Dokumentumok/Claude/google-workspace` mappát** minden sessionhöz.

Miért működik? Az MCP-szervereket a **Claude Desktop indítja a host gépen**, nem
a Cowork-sandboxban. Így a szerver közvetlenül látja a credential-fájljaidat a
host filerendszerről, a credentialök elérési útját / tartalmát pedig a
`claude_desktop_config.json` env blokkjából kapja. Minden sessionben automatikusan
elérhető, nulla kézi lépéssel.

A teljes Drive / Docs / Sheets / Gmail funkciókészlet ugyanaz, mint a skillé — a
tényleges API-logika a `google_helper.py`-ban van, a `server.py` csak betölti a
credentialöket és MCP-tool-ként közzéteszi a függvényeket.

## Mire van szükség

- A meglévő credential-fájljaid: `google_token.json` és `google_client_secret.json`
  (ezek már megvannak a `~/Dokumentumok/Claude/google-workspace` mappádban az
  eredeti OAuth-setupból).
- Python 3.10+ a host gépen.

## Telepítés

1. Másold ezt a `google-workspace-mcp` mappát egy állandó helyre, pl.
   `~/Dokumentumok/Claude/google-workspace-mcp`.

2. Hozz létre egy virtuális környezetet és telepítsd a függőségeket:

   ```bash
   cd ~/Dokumentumok/Claude/google-workspace-mcp
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. Regisztráld a szervert a `claude_desktop_config.json`-ban (Claude Desktop →
   Settings → Developer → Edit Config, vagy macOS-en
   `~/Library/Application Support/Claude/claude_desktop_config.json`).

   **A) Egyszerű mód — a meglévő mappádra mutatsz** (ajánlott; a titkok fájlban
   maradnak, nem a configban):

   ```json
   {
     "mcpServers": {
       "google-workspace": {
         "command": "/ABSZOLUT/UT/google-workspace-mcp/.venv/bin/python",
         "args": ["/ABSZOLUT/UT/google-workspace-mcp/server.py"],
         "env": {
           "GOOGLE_WORKSPACE_DIR": "/ABSZOLUT/UT/Dokumentumok/Claude/google-workspace"
         }
       }
     }
   }
   ```

   **B) Minden a configban — a te eredeti ötleted** (a credentialök tartalma
   inline kerül be; nem függ semmilyen mappától):

   ```json
   {
     "mcpServers": {
       "google-workspace": {
         "command": "/ABSZOLUT/UT/google-workspace-mcp/.venv/bin/python",
         "args": ["/ABSZOLUT/UT/google-workspace-mcp/server.py"],
         "env": {
           "GOOGLE_TOKEN_JSON": "{\"access_token\":\"...\",\"refresh_token\":\"...\",\"scope\":\"...\"}",
           "GOOGLE_CLIENT_SECRET_JSON": "{\"installed\":{\"client_id\":\"...\",\"client_secret\":\"...\"}}"
         }
       }
     }
   }
   ```

   A `command` legyen a venv pythonja (abszolút út). A `/ABSZOLUT/UT/...` részeket
   cseréld a saját útjaidra.

4. Indítsd újra a Claude Desktopot. A `google-workspace` szerver tooljai
   (search_files, read_doc, write_sheet, gmail_search, create_draft, stb.) ezután
   minden sessionben elérhetők.

## Credential-betöltési sorrend

A `server.py` ebben a sorrendben keres:

1. `GOOGLE_WORKSPACE_DIR` — mappa, amiben a két JSON fájl van (A mód).
2. `GOOGLE_TOKEN_JSON` + `GOOGLE_CLIENT_SECRET_JSON` — a fájlok tartalma inline
   (B mód). Induláskor egy privát temp mappába íródnak.
3. Fallback: a helper saját auto-felderítése (a home könyvtárban keres egy
   `google-workspace` mappát).

## Token-frissítés

A szerver hosszú életű, ezért a frissített access tokent memóriában tartja a
process futása alatt — a refresh token nem változik. **A mód** esetén a frissített
token vissza is íródik a mappa `google_token.json` fájljába. **B mód** esetén a
frissítés csak a process élettartamáig él (a refresh token a configban marad, így
ez nem gond).

Ha `401`-et kapsz a refresh ellenére, a refresh token érvénytelen, és újra kell
futtatni az eredeti OAuth-flow-t.

## Megjegyzés a régi skillről

Ha ezt használod, a `google-workspace` skillt akár ki is kapcsolhatod, hogy ne
ütközzön / ne kérje a mappa mountolását. A Gmail-műveletekhez továbbra is a
megfelelő OAuth scope-ok kellenek (`gmail.modify`, a végleges törléshez a teljes
`mail.google.com`).

## Biztonsági megjegyzés

A `GOOGLE_TOKEN_JSON` egy refresh tokent tartalmaz, ami tartós hozzáférést ad a
fiókodhoz. Akár fájlban (A), akár configban (B) tárolod, kezeld titkosan: ne tedd
verziókövetésbe, és korlátozd a fájl jogosultságait.
