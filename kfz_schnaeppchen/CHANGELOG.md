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
