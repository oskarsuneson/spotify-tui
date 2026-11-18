# Terminal-Spotify

En musikspelare i terminalen: stapeldiagram som följer ljudet, albumomslag, låttext, förloppsbar och kontroller. Färgen följer albumomslaget. Fungerar på Windows och Linux.

## Du behöver
- Python 3.10+
- Spotify Premium (Spotify kräver det för att skapa utvecklarappen och för att styra uppspelning)
- Ett eget **Client ID** (gratis, se nedan). Det finns inget ID i koden, så alla använder sitt eget.

## Installera
    pip install -r requirements.txt
    python app.py

Linux: installera även `libpulse0` (t.ex. `sudo apt install libpulse0`).
Windows: använd Windows Terminal.

## Första gången: skaffa ett Client ID
1. Gå till https://developer.spotify.com/dashboard och välj **Create app**.
2. Redirect URI: `http://127.0.0.1:8888/callback`, kryssa i **Web API**, spara.
3. Öppna **Settings** i appen och kopiera **Client ID**.
4. Starta `python app.py`. Du får klistra in ID:t en gång. Det sparas i din användarmapp (inte i projektmappen) och en webbläsare öppnas för inloggning.

ID:t kan bytas senare i appen: tryck `s` (Inställningar). Starta om appen efter byte.

## Tangenter
| Tangent | Funktion |
|---|---|
| mellanslag | start/stopp |
| n / p | nästa / förra |
| ↑ ↓ | ljud |
| ← → | spola 10 s |
| l | visa/dölj låttext |
| c | byt färg (auto följer albumomslaget) |
| s | inställningar (Client ID, färg, bildläge, låttext) |
| q | avsluta |

## Bildkvalitet
Som standard ritas omslaget med färgade block, vilket fungerar i alla terminaler. Terminaler som stöder Sixel eller Kitty-protokollet kan visa äkta bild: kör `pip install textual-image`, tryck `s` och välj **Bildläge: äkta bild**, starta om appen. Stöd saknas i många terminaler (t.ex. ChromeOS Terminal), och då ska du välja tillbaka till block.

## Obs
- Staplarna analyserar datorns systemljud, så musiken måste spelas på samma dator. Spelas den på mobilen rör sig inte staplarna.
- Låttext hämtas från LRCLIB (lrclib.net).
- Inställningar och inloggning sparas utanför projektmappen, så inget känsligt hamnar på GitHub.

## Starta enklare
- Windows: dubbelklicka på `start.bat`
- Linux: `./start.sh` (kör vid behov `chmod +x start.sh` först)
