# Getting started

Three things to set up once, then your rides land in the app by themselves and your coach can talk to you.

```
Strava  ──►  intervals.icu  ──►  Train Like a Pro
                                       ▲
                              Google Gemini key
```

You need about 10 minutes. Everything below is free.

---

## 1. Connect Strava to intervals.icu

intervals.icu pulls your rides from Strava and keeps them in sync.

1. Go to [intervals.icu](https://intervals.icu) and choose **Sign in with Strava**.
2. Confirm the Strava permission screen.
3. Wait for the first import to finish — intervals.icu loads your history and shows your rides in its calendar.

From now on, every new Strava ride shows up in intervals.icu automatically.

> **No Strava?** You can skip this step. intervals.icu also accepts other sources, and Train Like a Pro accepts FIT file uploads directly.

---

## 2. Connect intervals.icu to Train Like a Pro

You need two values from intervals.icu: an **API key** and your **athlete ID**.

1. In intervals.icu, open **Settings** (your name, top right) and scroll to **Developer Settings**.
2. Copy the **API Key**.
3. Note your **athlete ID** in the same place — it looks like `i123456`.
4. In Train Like a Pro, open **Settings → Data Sources**.
5. Paste the API key into **API Key**, put your athlete ID into **Athlete ID**, and press **Connect Intervals.icu**.
   - Leave `Athlete ID` at `0` if you cannot find yours — that means "the athlete this key belongs to" and works fine.
   - **Athlete Name** is optional.
6. Switch **Automatic sync** on.

You should see a green **Connected to Intervals.icu**. Your rides start importing right away.

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

Open the coach chat and tell it what you are training for, how your typical week looks, and anything it should know — an event date, an injury, the days you cannot ride. That conversation is what every future session is built on.

---

## Something not working?

| Symptom | Fix |
| --- | --- |
| No rides appear after connecting | Check that the ride exists in intervals.icu first. Train Like a Pro only sees what intervals.icu has. |
| "Could not save the Intervals.icu connection" | The API key is wrong or was copied with a trailing space. Copy it again from **Developer Settings**. |
| Coach replies with an error | Your Gemini key is missing, expired, or out of quota. Re-test it under **Settings → AI Provider**. |
| Rides are in intervals.icu but not here | Make sure **Automatic sync** is on under **Settings → Data Sources**. |

---

*Deutsche Fassung: [erste-schritte.md](erste-schritte.md)*
