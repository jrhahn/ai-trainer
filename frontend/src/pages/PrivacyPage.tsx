import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useLegalContact } from '../hooks/useLegalContact'
import { useLanguage, useT } from '../i18n'

function Section({ id, title, children }: { id?: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="mt-8">
      <h2 className="text-lg font-semibold text-gray-900">{title}</h2>
      <div className="mt-2 space-y-3 text-sm leading-6 text-gray-700">{children}</div>
    </section>
  )
}

function Mail({ email, missing }: { email: string | null | undefined; missing: string }) {
  return email ? (
    <a className="text-amber-700 underline" href={`mailto:${email}`}>
      {email}
    </a>
  ) : (
    <em>{missing}</em>
  )
}

/**
 * The privacy policy (ai-trainer-ops#2) and the note on training advice (#5).
 *
 * Written for what this app does, read off its code on 2026-10-18: the services
 * named below are the ones the backend calls. Change the text when that
 * changes. Reachable signed in and signed out, and linked from the sign-up
 * form, where the consent to health data is given.
 */
export default function PrivacyPage() {
  const t = useT()
  const language = useLanguage()
  const contact = useLegalContact()
  const name = contact?.operatorName
  const email = contact?.contactEmail
  const review = contact?.coachReviewOptIn === true

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
        <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800">
          <ArrowLeft size={16} aria-hidden="true" /> {t('Back')}
        </Link>
        {language === 'de' ? (
          <German name={name} email={email} review={review} />
        ) : (
          <English name={name} email={email} review={review} />
        )}
      </div>
    </main>
  )
}

type Who = { name: string | null | undefined; email: string | null | undefined; review?: boolean }

/**
 * The weekly coach review an athlete can consent to in Settings
 * (ai-trainer-ops#136). Shown only while the opt-in is switched on; the facts
 * are checked against the agreement with Anthropic (ops#3) before that.
 */
function CoachReviewGerman() {
  return (
    <Section id="coach-review" title="Coach-Review (nur mit deiner Einwilligung)">
      <p>
        <strong>Zweck:</strong> Einmal pro Woche wird geprüft, ob der Coach gut beraten hat, um ihn zu verbessern.
      </p>
      <p>
        <strong>Daten:</strong> deine Chatnachrichten, Planänderungen und Fahrten der Woche, ohne Name, E-Mail,
        Kennungen und Ortsdaten. Darunter sind Gesundheitsangaben. Gelesen wird nur, was nach deiner Einwilligung
        geschieht.
      </p>
      <p>
        <strong>Empfänger:</strong> Anthropic als Auftragsverarbeiter, USA. Grundlage ist das EU-US Data Privacy
        Framework beziehungsweise die Standardvertragsklauseln der EU-Kommission.
      </p>
      <p>
        <strong>Was bleibt:</strong> Zählungen über mindestens drei Sportler und umschriebene Befunde, ohne Zitate
        und ohne etwas, das auf dich zurückführt.
      </p>
      <p>
        <strong>Rechtsgrundlage:</strong> deine Einwilligung, Art. 6 Abs. 1 lit. a und Art. 9 Abs. 2 lit. a DSGVO.
      </p>
      <p>
        <strong>Widerruf:</strong> jederzeit in den Einstellungen unter „Deine Daten“. Er gilt ab dem nächsten Lauf.
      </p>
    </Section>
  )
}

function CoachReviewEnglish() {
  return (
    <Section id="coach-review" title="Coach review (only with your consent)">
      <p>
        <strong>Purpose:</strong> once a week, a check of whether the coach advised you well, to improve it.
      </p>
      <p>
        <strong>Data:</strong> the week's chat messages, plan changes and rides, without your name, email, ids or
        location. They include health data. Only what happens after your consent is read.
      </p>
      <p>
        <strong>Recipient:</strong> Anthropic as processor, USA, on the basis of the EU-US Data Privacy Framework or
        the European Commission's standard contractual clauses.
      </p>
      <p>
        <strong>What is kept:</strong> counts over at least three athletes and paraphrased findings, with no quotes
        and nothing that points to you.
      </p>
      <p>
        <strong>Legal basis:</strong> your consent, Art. 6(1)(a) and Art. 9(2)(a) GDPR.
      </p>
      <p>
        <strong>Withdrawal:</strong> at any time in Settings under "Your Data". It applies from the next run on.
      </p>
    </Section>
  )
}

function German({ name, email, review }: Who) {
  return (
    <article>
      <h1 className="mt-6 text-2xl font-bold text-gray-900">Datenschutzerklärung</h1>
      <p className="mt-2 text-sm text-gray-500">Stand: Oktober 2026</p>

      <Section title="Wer verantwortlich ist">
        <p>
          Train Like a Pro wird derzeit als privates, nicht kommerzielles Projekt in einer geschlossenen Testphase
          betrieben. Verantwortlich ist {name ?? <em>(noch nicht eingetragen)</em>}. Fragen zu deinen Daten und
          Probleme mit der App an: <Mail email={email} missing="(noch nicht eingetragen)" />.
        </p>
      </Section>

      <Section title="Wofür die App deine Daten braucht">
        <p>
          Die App erstellt aus deinen Angaben und deinem Training einen Trainingsplan, passt ihn laufend an und
          beantwortet deine Fragen im Chat. Dafür verarbeitet sie nur, was du eingibst, verbindest oder hochlädst.
        </p>
      </Section>

      <Section title="Welche Daten">
        <ul className="list-disc space-y-1 pl-5">
          <li>Konto: Name, E-Mail-Adresse, Passwort (nur als Hash gespeichert).</li>
          <li>
            Profil: Sportarten, Trainingstage, Ziele und Rennen, Leistungsniveau, FTP, maximale Herzfrequenz und
            dein üblicher Trainingsort.
          </li>
          <li>
            Training: deine Aktivitäten mit Datum, Dauer, Distanz, Höhenmetern, Leistung und Herzfrequenz, aus
            intervals.icu oder Strava, aus hochgeladenen FIT-Dateien oder von dir eingetragen.
          </li>
          <li>
            Rückmeldungen: empfundene Anstrengung, Notizen, und was du über deinen Zustand sagst, etwa krank, müde
            oder verletzt.
          </li>
          <li>Der Chat mit dem Coach und was der Coach daraus über dich festhält.</li>
          <li>
            Technisch Nötiges: ein Sitzungscookie für die Anmeldung und Server-Protokolle (unter anderem die
            IP-Adresse) zur Fehlersuche und zum Schutz vor Missbrauch.
          </li>
        </ul>
      </Section>

      <Section title="Gesundheitsdaten">
        <p>
          Herzfrequenz, Anstrengung und Angaben zu Krankheit, Müdigkeit oder Verletzungen sind Gesundheitsdaten
          (Art. 9 DSGVO). Die App verarbeitet sie nur mit deiner ausdrücklichen Einwilligung, die du bei der
          Registrierung gibst (Art. 9 Abs. 2 lit. a DSGVO). Du kannst sie jederzeit widerrufen, indem du dein Konto
          löschst oder eine E-Mail an die oben genannte Adresse schreibst. Bis dahin Verarbeitetes bleibt
          rechtmäßig.
        </p>
      </Section>

      <Section title="Rechtsgrundlagen">
        <ul className="list-disc space-y-1 pl-5">
          <li>Bereitstellung der App, die du nutzen möchtest: Art. 6 Abs. 1 lit. b DSGVO.</li>
          <li>Gesundheitsdaten: deine Einwilligung, Art. 6 Abs. 1 lit. a und Art. 9 Abs. 2 lit. a DSGVO.</li>
          <li>Sicherheit und Abwehr von Missbrauch: berechtigtes Interesse, Art. 6 Abs. 1 lit. f DSGVO.</li>
        </ul>
      </Section>

      <Section title="Wer Daten erhält">
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <strong>Hetzner Online GmbH</strong>, Deutschland: Server, auf denen die App und ihre Datenbank laufen,
            in der EU.
          </li>
          <li>
            <strong>Google (Gemini API)</strong>: Für Pläne und Chat-Antworten gehen dein Profil, dein Training,
            deine Rückmeldungen und der Chat an Googles KI-Dienst. Dabei können Daten in die USA übermittelt werden.
            Grundlage ist das EU-US Data Privacy Framework beziehungsweise die Standardvertragsklauseln der
            EU-Kommission.
          </li>
          <li>
            <strong>OpenAI</strong>: nur, wenn du in den Einstellungen deinen eigenen OpenAI-Schlüssel hinterlegst.
            Dann gilt dasselbe wie für Google.
          </li>
          <li>
            <strong>Scaleway</strong>, Frankreich: Versand von E-Mails, etwa zur Bestätigung der Adresse und zum
            Zurücksetzen des Passworts.
          </li>
          <li>
            <strong>Open-Meteo</strong>: Für die Wettervorhersage gehen die Koordinaten deines Trainingsorts dorthin,
            für die Ortssuche der Ortsname, den du eingibst.
          </li>
          <li>
            <strong>intervals.icu und Strava</strong>: nur, wenn du sie verbindest. Die App ruft dann deine
            Aktivitäten von dort ab.
          </li>
        </ul>
        <p>Es gibt keine Werbung, kein Tracking und keine Analyse-Cookies. Deine Daten werden nicht verkauft.</p>
      </Section>

      {review && <CoachReviewGerman />}

      <Section title="Wie lange">
        <p>
          Solange dein Konto besteht. Löschst du es in den Einstellungen, werden deine Daten aus der App sofort
          gelöscht. In Sicherungskopien bleiben sie bis zu deren regelmäßiger Überschreibung.
        </p>
      </Section>

      <Section title="Deine Rechte">
        <p>
          Du hast das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung, Datenübertragbarkeit
          und Widerspruch sowie auf Widerruf deiner Einwilligung. Vieles geht direkt in der App: Unter Einstellungen
          kannst du alle deine Daten exportieren und dein Konto löschen. Für alles andere schreib an{' '}
          <Mail email={email} missing="(noch nicht eingetragen)" />. Du kannst dich außerdem bei einer
          Datenschutz-Aufsichtsbehörde beschweren.
        </p>
      </Section>

      <Section id="training" title="Hinweis zum Training">
        <p>
          Der Coach ist eine Software, die mit KI arbeitet. Er ist keine Ärztin und kein Arzt und ersetzt keine
          medizinische Beratung. Seine Pläne und Antworten können falsch sein. Trainiere so, wie es sich für dich
          richtig anfühlt. Bei Schmerzen, Schwindel, Brustschmerzen oder Atemnot brich das Training ab und lass es
          ärztlich abklären. Wenn du eine Erkrankung hast oder lange nicht trainiert hast, sprich vor dem Start mit
          einer Ärztin oder einem Arzt.
        </p>
      </Section>
    </article>
  )
}

function English({ name, email, review }: Who) {
  return (
    <article>
      <h1 className="mt-6 text-2xl font-bold text-gray-900">Privacy policy</h1>
      <p className="mt-2 text-sm text-gray-500">As of October 2026</p>

      <Section title="Who is responsible">
        <p>
          Train Like a Pro is currently run as a private, non-commercial project in a closed test. The controller
          is {name ?? <em>(not set yet)</em>}. Questions about your data and problems with the app go to{' '}
          <Mail email={email} missing="(not set yet)" />.
        </p>
      </Section>

      <Section title="What the app needs your data for">
        <p>
          The app builds a training plan from what you tell it and from your training, keeps adjusting it, and
          answers your questions in the chat. For that it processes only what you enter, connect or upload.
        </p>
      </Section>

      <Section title="Which data">
        <ul className="list-disc space-y-1 pl-5">
          <li>Account: name, email address, password (stored only as a hash).</li>
          <li>
            Profile: sports, training days, goals and races, fitness level, FTP, maximum heart rate and your usual
            training location.
          </li>
          <li>
            Training: your activities with date, duration, distance, elevation, power and heart rate, from
            intervals.icu or Strava, from uploaded FIT files, or entered by you.
          </li>
          <li>Feedback: perceived effort, notes, and what you say about how you are, such as ill, tired or injured.</li>
          <li>The chat with the coach, and what the coach notes about you from it.</li>
          <li>
            Technical necessities: a session cookie for signing in, and server logs (including the IP address) to
            find faults and prevent abuse.
          </li>
        </ul>
      </Section>

      <Section title="Health data">
        <p>
          Heart rate, effort, and what you say about illness, fatigue or injury are health data (Art. 9 GDPR). The
          app processes them only with your explicit consent, given when you sign up (Art. 9(2)(a) GDPR). You can
          withdraw it at any time by deleting your account or by writing to the address above. Processing up to
          then remains lawful.
        </p>
      </Section>

      <Section title="Legal bases">
        <ul className="list-disc space-y-1 pl-5">
          <li>Providing the app you want to use: Art. 6(1)(b) GDPR.</li>
          <li>Health data: your consent, Art. 6(1)(a) and Art. 9(2)(a) GDPR.</li>
          <li>Security and abuse prevention: legitimate interest, Art. 6(1)(f) GDPR.</li>
        </ul>
      </Section>

      <Section title="Who receives data">
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <strong>Hetzner Online GmbH</strong>, Germany: the servers the app and its database run on, in the EU.
          </li>
          <li>
            <strong>Google (Gemini API)</strong>: for plans and chat replies, your profile, training, feedback and
            the chat go to Google's AI service. Data may be transferred to the USA, on the basis of the EU-US Data
            Privacy Framework or the European Commission's standard contractual clauses.
          </li>
          <li>
            <strong>OpenAI</strong>: only if you add your own OpenAI key in Settings. The same then applies as for
            Google.
          </li>
          <li>
            <strong>Scaleway</strong>, France: sending emails, such as confirming your address and resetting your
            password.
          </li>
          <li>
            <strong>Open-Meteo</strong>: the coordinates of your training location for the weather forecast, and the
            place name you type for the place search.
          </li>
          <li>
            <strong>intervals.icu and Strava</strong>: only if you connect them. The app then fetches your activities
            from there.
          </li>
        </ul>
        <p>There is no advertising, no tracking and no analytics cookie. Your data is not sold.</p>
      </Section>

      {review && <CoachReviewEnglish />}

      <Section title="How long">
        <p>
          For as long as your account exists. When you delete it in Settings, your data is removed from the app at
          once. Backups keep it until they are overwritten in the regular cycle.
        </p>
      </Section>

      <Section title="Your rights">
        <p>
          You have the right of access, rectification, erasure, restriction, portability and objection, and to
          withdraw your consent. Much of it works in the app: in Settings you can export all your data and delete
          your account. For everything else, write to <Mail email={email} missing="(not set yet)" />. You can also
          lodge a complaint with a data protection supervisory authority.
        </p>
      </Section>

      <Section id="training" title="A note on training">
        <p>
          The coach is software that uses AI. It is not a doctor and does not replace medical advice. Its plans and
          replies can be wrong. Train the way that feels right for you. If you have pain, dizziness, chest pain or
          shortness of breath, stop and get it checked by a doctor. If you have a medical condition or have not
          trained for a long time, talk to a doctor before you start.
        </p>
      </Section>
    </article>
  )
}
