# Getting started

Three things to set up once, then your rides land in the app by themselves and your coach can talk to you.

```
Device (Garmin, Wahoo …)  ──►  intervals.icu  ──►  Train Like a Pro
                                                         ▲
                                                Google Gemini key
```

You need about 10 minutes. Everything below is free.

---

## 1. Connect your device to intervals.icu

intervals.icu pulls your rides from the device or app you record with and keeps them in sync.

1. Go to [intervals.icu](https://intervals.icu) and create an account. It is free.
2. Under **Settings → Connections**, connect your device or app: Garmin Connect, Wahoo, Zwift, COROS, Suunto, Polar …
3. Wait for the first import to finish. intervals.icu then shows your rides in its calendar.

From now on, every new ride shows up in intervals.icu automatically.

> **Strava alone is not enough.** Strava forbids other services to pass its data on, so intervals.icu does not hand rides that come only from Strava to Train Like a Pro. You can keep using Strava as before. Older rides that are only on Strava come in through your Strava archive: **Import All Strava Data** in the Strava box of the connections.
>
> **No device?** Train Like a Pro also takes FIT files directly by upload.

---

## 2. Connect intervals.icu to Train Like a Pro

All you need from intervals.icu is your **API key**.

1. In intervals.icu, open **Settings** and scroll all the way down to **Developer Settings**.
2. Next to **API Key**, click **(view)** and copy the key. If the field is empty, generate one there. Do not clear it: that deletes the key.
3. In Train Like a Pro, open **Settings → Activities**.
4. Paste the key into **API Key** and press **Connect Intervals.icu**. You do not need an athlete ID.
5. Leave **Turn on automatic sync with Intervals.icu** switched on.

You then see **Connected to Intervals.icu**, and your rides start importing right away.

---

## 3. Add your Google Gemini key

Your coach runs on your own API key, so your AI usage is billed to your account and never shared. Gemini has a free tier that is enough for normal training use.

1. Go to [Google AI Studio](https://aistudio.google.com/apikey) and sign in with a Google account.
2. Click **Create API key** and pick a Google Cloud project (or let it create one).
3. Copy the key — it starts with `AIza…`. You cannot view it again later, so paste it into the app now.
4. In Train Like a Pro, open **Settings → AI Provider**.
5. Choose **Google Gemini**, paste the key, and press **Test** to check it.
6. Press **Save**. The badge changes to **Key saved**.

The key is encrypted at rest and is never sent back out of the app.

---

## 4. Tell your coach who you are

Open the chat with your coach and say what you are training for, how your typical week looks, and anything else your coach should know — an event date, an injury, the days you cannot ride. That conversation is what every future session is built on.

---

## Something not working?

| Symptom | Fix |
| --- | --- |
| No rides appear after connecting | Check that the ride exists in intervals.icu first. Train Like a Pro only sees what intervals.icu has, and no ride that came only through Strava (step 1). |
| "Could not save the Intervals.icu connection" | The API key is wrong or was copied with a trailing space. Copy it again from **Developer Settings**. |
| Coach replies with an error | Your Gemini key is missing, expired, or out of quota. Re-test it under **Settings → AI Provider**. |
| Rides are in intervals.icu but not here | Make sure **Turn on automatic sync with Intervals.icu** is on under **Settings → Activities**. |

---

*Deutsche Fassung: [erste-schritte.md](erste-schritte.md)*
