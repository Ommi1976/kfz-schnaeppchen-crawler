# 1.5.1

- AutoUncle-Treffer führen wieder direkt zum Inserat beim Händler bzw. Ursprungsportal. Rund ein Drittel der Ergebniskarten enthält diesen Link nicht; das Add-on holt ihn jetzt von AutoUncles Fahrzeugseite („Zum Angebot“, höchstens 15 Seiten je Lauf) und merkt ihn sich.

# 1.5.0

- Gelöschte Inserate verschwinden zuverlässig aus allen Trefferlisten. Bisher markierte nur mobile.de verschwundene Inserate; bei AutoScout24, AutoUncle und Kleinanzeigen blieben sie für immer stehen.
- Im Lauf nicht gesehene Inserate werden einzeln geprüft: HTTP 404/410 (AutoScout24, AutoUncle) bzw. Umleitung auf eine Suchseite (Kleinanzeigen) gelten als gelöscht. Gemessen an Produktionsdaten; Seitentexte wie „verkauft“ werden nicht ausgewertet.
- Gelöschte Inserate erscheinen auch nicht mehr in der Ansicht mit veralteten Einträgen und nicht mehr als Angebot auf einem anderen Portal.
- Sicherheitsnetz für mobile.de und nicht eindeutig prüfbare Inserate: 72 Stunden nicht gesehen, obwohl das Portal Treffer lieferte, und nicht als vorhanden bestätigt → ausgeblendet.

# 1.4.2

- Verbundene Portale (z. B. AutoScout24): „Portal-Browserabruf unterbrochen“ nennt jetzt die Fehlerart, das Log zusätzlich die erste Zeile der Ursache. Bisher wurde sie verschluckt.
- Nach einem solchen Fehler wird der Portal-Browser geschlossen und beim nächsten Abruf neu gestartet, wie beim mobile.de-Browser. Das Profil und die Anmeldung bleiben erhalten.

# 1.4.1

- mobile.de-Chrome meldet jetzt Deutsch (`de-DE`) als Sprache statt `en-US`. Unter Linux ignoriert Chrome `--lang`; die Sprache kommt aus `LANG`, das im Add-on nicht gesetzt war.
- Der erste Seitenabruf wartet, bis der Compositor das Chrome-Fenster maximiert hat (1280×577 statt kurzzeitig 1018×515).

# 1.4.0

- mobile.de läuft jetzt in Google Chrome auf der Intel-GPU des Servers statt in Firefox auf Xvfb. Xvfb rendert in Software, was Akamai als Merkmal erkennen kann. Gemessen im Container: `navigator.webdriver=false`, WebGL meldet die echte Intel-GPU, kein CDP-Leck; ein Probeabruf lieferte eine vollständige Trefferliste.
- Neue Option `mobile_gpu_browser` (Standard an). Ohne `/dev/dri/renderD128` oder bei ausgeschaltetem Schalter bleibt mobile.de bei Firefox. Die übrigen Portale nutzen weiterhin Firefox.
- Neues Browserprofil `/data/chrome_profile`: Eine mobile.de-Anmeldung muss im Add-on einmal neu hergestellt werden.
- Das Anmeldefenster übernimmt die tatsächliche Fenstergröße des Browsers.
- Sitzungstiefe, Seitenbudget und Suchpausen sind unverändert.

# 1.3.4

- AutoScout24 lieferte keine Treffer mehr: Beim Abruffehler brach die Suche mit „name 'logger' is not defined“ ab und verdeckte den eigentlichen Fehler. Scheitert jetzt schon die erste Seite, erscheint der echte Abruffehler im Suchfortschritt; bei späteren Seiten bleiben die bis dahin gefundenen Treffer erhalten.
- Suchfortschritt: Die Spalte „Anbieter gemeldet“ erscheint nur, wenn ein Portal diese Zahl liefert. Fehlende Werte stehen als „–“ statt „unbekannt“.
- Optionen: Der Telegram-Bot-Token ist jetzt ein verdecktes Passwortfeld; die Chat-ID ist ein normales Textfeld (die Typen waren vertauscht).

# 1.3.3

- Texteingabe im Anmeldefenster verliert bei automatischen Bildschirmaktualisierungen nicht mehr den Fokus.
- Noch nicht gesendeter Text bleibt bei einem Sendeversuch während der Bildaktualisierung erhalten. Portalaktionen werden weiterhin nacheinander ausgeführt.
- Browser-Regression prüft langsames Tippen über mehrere Aktualisierungen und einen Sendeversuch während eines verzögerten Bildabrufs.

# 1.3.2

- mobile.de-Verbindung startet über den offiziellen Käufer-Login auf www.mobile.de mit Weiterleitung zu id.mobile.de, nicht mehr über die Suchverwaltung auf suchen.mobile.de.
- Harte Zugriffsverweigerungen werden als „Zugriff blockiert“ angezeigt und nicht mehr als lösbare Bestätigungsaufgabe.
- Auf einer Sperrseite werden Zugangsdaten-Eingabe und Bedienhinweise ausgeblendet; der Server verhindert ebenfalls die Weitergabe von Eingaben an diese Seite.
- Tests prüfen Anmeldeeinstieg, Sperrerkennung, Eingabeschutz und Wiederherstellung der Bedienung nach einem Seitenwechsel. Browserprofile und Suchpausen bleiben unverändert.

Eine erreichbare Anmeldung bestätigt noch keinen erfolgreichen Suchabruf. Der Portalbetreiber kann auch den neuen Anmeldeweg oder die anschließende Suche blockieren.

# 1.3.1

- Trefferliste repariert: Ein Inserat mit SoH-Wert konnte die gesamte Darstellung abbrechen.
- Fehler werden sichtbar angezeigt; vorhandene Treffer bleiben bei Abruffehlern erhalten. Statusfehler verhindern den Trefferabruf nicht mehr.
- Nachgewiesene mobile.de-Angebote aus AutoUncle sind über den mobile.de-Filter erreichbar, mit Herkunftshinweis und unveränderter AutoUncle-URL.
- Direkter Abruf und indirekte Angebote werden getrennt gezählt; in „Alle Portale“ wird kein zusätzlicher Datensatz erzeugt.
- Bereits gespeicherte AutoUncle-Weiterleitungen werden ohne neue Portalabrufe zugeordnet; neue Karten speichern ihren ausdrücklichen Quellenhinweis.
- Browser-Regressionstests für SoH, Filter und Fehlerbehandlung werden auch in CI ausgeführt.

Der mobile.de-Direktabruf kann weiterhin blockiert sein. Die AutoUncle-Erfassung ist unabhängig davon automatisiert, aber kein vollständiger Spiegel des mobile.de-Bestands.

# 1.3.0

- Portalkonten für mobile.de, AutoScout24, Kleinanzeigen und AutoUncle in der Add-on-Oberfläche.
- Interaktive Anmeldung direkt im HAOS-Browser mit kurzlebiger, geschützter Bildschirmansicht.
- Anmeldung und Suche verwenden dasselbe persistente Portalprofil; kein externer Desktop-Browser nötig.
- Getrennte Nachweise für Anmeldung, erfolgreichen Suchabruf und Vollständigkeit; Cookies allein gelten nicht als Nachweis.
- Suchfortschritt mit Seiten, lokalen Ausschlüssen und offenen Teilabrufen.
- Konto-Verbindung prüfen, Anmeldefenster schließen und lokale Sitzung entfernen.
- Keine Passwortspeicherung in den Einstellungen; alle Konto-API-Zugriffe geschützt, Ingress-Header gegen LAN-Spoofing abgesichert.

Die persönliche Portal-Anmeldung (einschließlich ggf. 2FA/Verifikation) erfolgt durch den Nutzer.
Eine Anmeldung hebt Suchpausen nicht auf und garantiert keinen vollständigen Portalzugriff.

# 1.2.29

- Autonome, wiederverwendbare mobile.de-Sitzung im HA-Add-on ohne Desktop-Cookie-Abhängigkeit.
- Native Akku-/ACC-Filter korrigiert; Reichweitenstufen nach außen gerundet und lokal exakt geprüft.
- Tolerante Zusatzsuche ohne Akku-Portalfilter, fortsetzbare Vollsuchen und kurze Aktualisierungsläufe.
- Portalweite Abrufbudgets und persistente Schutzpausen für Suche, Details und Zertifikatsbilder.
- Seitenende, angewendete EV-Filter und Seitenwechsel geprüft; kein Bestandsverlust durch Teilabrufe.
- Priorisierte Detailprüfung einschließlich Altbestand; SoH, Akku, WLTP und Erstzulassung aus Fahrzeugdaten statt Empfehlungen.
- Gespeicherte Akku-Bezugsgrößen und SoH-Belege bleiben bei der Nachfilterung erhalten.
- Neue Statusanzeige, Betriebsdokumentation und Regressionstests.

Automatischer Zugriff bleibt von der Erreichbarkeit und Freigabe des Portals abhängig.
Eine vollständige oder dauerhaft blockfreie Suche wird nicht zugesichert.
