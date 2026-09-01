# mobile.de-Sitzung an das Add-on übertragen

mobile.de ist durch den Akamai Bot Manager geschützt. Ein automatisierter
Abruf ohne gültige Sitzung wird abgewiesen. Diese Erweiterung überträgt die
Sitzungscookies aus **deinem** angemeldeten Browser an das Add-on – auf Wunsch
automatisch im Hintergrund.

## Was sie tut und was nicht

* Gelesen werden **ausschließlich** Cookies der Domain `mobile.de`.
* Gesendet wird **ausschließlich** an die von dir eingetragene Add-on-Adresse.
* **Keine Zugangsdaten** werden gespeichert oder übertragen – nur die Cookies
  einer Sitzung, in der du bereits angemeldet bist.
* Nichts geht an Dritte.

Die Erweiterung meldet dich nicht an. Sie setzt voraus, dass du in diesem
Browser bei mobile.de angemeldet bist.

## Einrichten

1. **Token holen:** Im Add-on-Protokoll steht beim Start die Zeile
   `Cookie-Token für die Browser-Erweiterung: …`
2. **Erweiterung laden:** `chrome://extensions` → Entwicklermodus einschalten →
   „Entpackte Erweiterung laden" → diesen Ordner wählen.
   (Edge: `edge://extensions`, gleicher Ablauf.)
3. **Einstellungen öffnen** (Rechtsklick auf das Symbol → Optionen):
   * Adresse: `http://<HA-IP>:8099`
   * Token: aus Schritt 1
   * „Sitzung automatisch auffrischen" aktivieren, Abstand z. B. 30 Minuten
4. Speichern. Der Browser fragt einmalig nach Zugriff auf genau diese Adresse.

## Betrieb

Mit aktiviertem Automatikmodus prüft die Erweiterung im eingestellten Abstand,
ob sich die Sitzung geändert hat, und sendet nur dann. Unveränderte Cookies
werden nicht erneut übertragen.

Über das Symbol lässt sich jederzeit manuell senden; dort steht auch, wann
zuletzt übertragen wurde.

## Grenzen

* Läuft nur, solange der Browser läuft.
* Das Add-on verwendet Cookies nur, wenn sie **jünger als zwölf Stunden** sind.
  Ohne laufenden Browser veraltet die Sitzung also und wird ignoriert.
* Meldest du dich bei mobile.de ab, wird die Sitzung ungültig.
