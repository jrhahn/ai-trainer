# Erste Schritte

Drei Dinge einmal einrichten — danach landen deine Fahrten von allein in der App und dein Coach kann mit dir arbeiten.

```
Strava  ──►  intervals.icu  ──►  Train Like a Pro
                                       ▲
                              Google-Gemini-Key
```

Du brauchst etwa 10 Minuten. Alles hier ist kostenlos.

> Die App-Oberfläche ist auf Englisch. Button- und Menünamen stehen deshalb unten im Original, damit du sie wiederfindest.

---

## 1. Strava mit intervals.icu verbinden

intervals.icu holt deine Fahrten aus Strava und hält sie aktuell.

1. Öffne [intervals.icu](https://intervals.icu) und wähle **Sign in with Strava**.
2. Bestätige die Berechtigungsseite von Strava.
3. Warte, bis der erste Import fertig ist — intervals.icu lädt deine Historie und zeigt die Fahrten im Kalender.

Ab jetzt taucht jede neue Strava-Fahrt automatisch in intervals.icu auf.

> **Kein Strava?** Dann überspring diesen Schritt. intervals.icu nimmt auch andere Quellen an, und Train Like a Pro akzeptiert FIT-Dateien direkt per Upload.

---

## 2. intervals.icu mit Train Like a Pro verbinden

Du brauchst zwei Angaben aus intervals.icu: einen **API Key** und deine **Athlete ID**.

1. Öffne in intervals.icu die **Settings** (dein Name, oben rechts) und scroll zu **Developer Settings**.
2. Kopiere den **API Key**.
3. Notiere dir an derselben Stelle deine **Athlete ID** — sie sieht aus wie `i123456`.
4. Öffne in Train Like a Pro **Settings → Data Sources**.
5. Füge den API Key bei **API Key** ein, trage deine Athlete ID bei **Athlete ID** ein und klick auf **Connect Intervals.icu**.
   - Lass bei **Athlete ID** die `0` stehen, falls du deine nicht findest — das bedeutet „die Athletin/der Athlet zu diesem Key" und funktioniert genauso.
   - **Athlete Name** ist optional.
6. Schalte **Automatic sync** ein.

Es sollte grün **Connected to Intervals.icu** erscheinen. Der Import deiner Fahrten startet sofort.

---

## 3. Google-Gemini-Key hinterlegen

Dein Coach läuft mit deinem eigenen API-Key — deine KI-Nutzung wird also über dein Konto abgerechnet und mit niemandem geteilt. Gemini hat ein kostenloses Kontingent, das für normales Training reicht.

1. Geh zu [Google AI Studio](https://aistudio.google.com/apikey) und melde dich mit einem Google-Konto an.
2. Klick auf **Create API key** und wähl ein Google-Cloud-Projekt (oder lass eins anlegen).
3. Kopier den Key — er beginnt mit `AIza…`. Später kannst du ihn nicht mehr ansehen, füg ihn also gleich in die App ein.
4. Öffne in Train Like a Pro **Settings → AI Provider**.
5. Wähl **Google Gemini**, füg den Key ein und klick **Test**, um ihn zu prüfen.
6. Klick **Save**. Das Abzeichen wechselt auf **Key saved**.

Der Key wird verschlüsselt gespeichert und verlässt die App nie wieder.

---

## 4. Erzähl deinem Coach, wer du bist

Öffne den Coach-Chat und sag ihm, worauf du hintrainierst, wie deine typische Woche aussieht und was er sonst wissen sollte — ein Termin, eine Verletzung, die Tage, an denen du nicht fahren kannst. Auf diesem Gespräch baut jede spätere Einheit auf.

---

## Etwas funktioniert nicht?

| Symptom | Lösung |
| --- | --- |
| Nach dem Verbinden erscheinen keine Fahrten | Prüf zuerst, ob die Fahrt in intervals.icu liegt. Train Like a Pro sieht nur, was intervals.icu hat. |
| „Could not save the Intervals.icu connection" | Der API Key ist falsch oder wurde mit Leerzeichen kopiert. Kopier ihn erneut aus den **Developer Settings**. |
| Der Coach antwortet mit einem Fehler | Dein Gemini-Key fehlt, ist abgelaufen oder das Kontingent ist aufgebraucht. Teste ihn erneut unter **Settings → AI Provider**. |
| Fahrten sind in intervals.icu, aber nicht hier | Stell sicher, dass **Automatic sync** unter **Settings → Data Sources** an ist. |

---

*English version: [getting-started.md](getting-started.md)*
