# mobile.de Cookie-Export (Edge/Chrome-Extension)

Exportiert deine mobile.de-Session-Cookies per Knopfdruck, damit das KFZ
Schnäppchen Add-on mobile.de **ohne Browser** abfragen kann (die Cookies stammen
aus deiner echten, eingeloggten Browser-Session; Akamai wird so umgangen).

## Installation in Edge
1. `edge://extensions/` öffnen.
2. **Entwicklermodus** (links) einschalten.
3. **Entpackte Erweiterung laden** → diesen Ordner (`browser-extension`) auswählen.

(In Chrome identisch unter `chrome://extensions/`.)

## Benutzung

### Variante A – Ein-Klick-Direktversand (empfohlen)
1. Im Add-on **mobile.de → „Cookies aktualisieren"** öffnen → **Token** kopieren.
   (Für den Direktzugriff in den **Add-on-Netzwerkeinstellungen** Port **8099**
   einem Host-Port zuweisen; die Add-on-URL ist dann `http://<HAOS-IP>:8099`.)
2. In der Extension unter **„Add-on-Verbindung"** die **URL** und den **Token** eintragen (einmalig).
3. In **mobile.de einloggen** → Extension → **„Senden"**. Fertig – die Cookies
   gehen direkt ans Add-on und werden sofort getestet.

### Variante B – Zwischenablage (ohne Netzwerk-Freigabe)
1. In mobile.de einloggen → Extension → **„Kopieren"**.
2. Im Add-on **mobile.de → „Cookies aktualisieren"** → einfügen → **Testen & Speichern**.

Wenn die Cookies ablaufen, zeigt das Add-on ein rotes Banner „Cookies abgelaufen".
Dann einfach erneut **„Senden"** (bzw. Kopieren/Einfügen).

## Datenschutz
Die Extension liest ausschließlich mobile.de-Cookies und kopiert sie in deine
Zwischenablage. Es werden keine Daten an Dritte gesendet.
