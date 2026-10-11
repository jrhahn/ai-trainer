import { Link } from "react-router-dom";
import { ArrowRight, Bike } from "lucide-react";
import RideLandscape from "../components/RideLandscape";
import { GITHUB_URL, SETUP_GUIDE_URL } from "../utils/links";

// Exported for the copy-voice guard in LandingPage.test.tsx (#610). Three short
// moments, a sentence or two each, with no cards or icons around them: the page
// should feel like the coach, quiet and attentive, rather than like a feature
// grid (ai-trainer-ops#67).
export const moments = [
  {
    title: "It already knows",
    body: "Every ride you sync shapes the next session, so the week fits how the last one actually went.",
  },
  {
    title: "It asks only when it matters",
    body: "A question comes when the answer would change your plan. The rest of the time it gets on with it.",
  },
  {
    title: "Talk to it if you want",
    body: "Tell it the legs are flat or ask what to eat before a long day. The plan moves with you.",
  },
];

export const facts = [
  "Free to use. You bring your own Google Gemini key.",
  "Rides come from Strava through intervals.icu. You can also upload a FIT file or enter your sessions yourself.",
];

export default function LandingPage() {
  return (
    <main className="landing min-h-screen bg-[#0f1116] text-white">
      <section className="relative isolate flex min-h-[92vh] flex-col overflow-hidden">
        <RideLandscape className="absolute inset-x-0 bottom-0 -z-10 h-[62%] w-full" />

        <header className="mx-auto flex w-full max-w-6xl items-center gap-4 px-5 py-5 sm:px-8">
          {/* `min-w-0` + `truncate` so the wordmark gives way at 390 px instead of
              the page scrolling sideways (ai-trainer-ops#51.3). */}
          <span className="flex min-w-0 items-center gap-2">
            <Bike
              size={22}
              aria-hidden="true"
              className="shrink-0 text-amber-400"
            />
            <span className="truncate text-base font-semibold tracking-tight sm:text-lg">
              Train Like a Pro
            </span>
          </span>
          <nav className="ml-auto flex shrink-0 items-center gap-4 sm:gap-6">
            <Link
              to="/login"
              className="whitespace-nowrap text-sm font-medium text-slate-300 hover:text-white"
            >
              Sign in
            </Link>
            <Link
              to="/register"
              className="whitespace-nowrap rounded-full border border-white/20 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-white/10"
            >
              Create account
            </Link>
          </nav>
        </header>

        <div className="mx-auto grid w-full max-w-6xl gap-12 px-5 pb-16 pt-14 sm:px-8 sm:pt-24 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div>
            <h1 className="max-w-[16ch] text-balance text-4xl font-semibold leading-[1.08] tracking-tight sm:text-6xl">
              A coach who already knows your riding.
            </h1>
            <p className="mt-6 max-w-lg text-lg leading-8 text-slate-300">
              It reads every ride you sync, so there is little to explain and
              nothing to fill in. Talk to it whenever you like.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-x-6 gap-y-3">
              <Link
                to="/register"
                className="inline-flex items-center gap-2 rounded-full bg-amber-500 px-6 py-3 text-base font-semibold text-[#0f1116] transition-colors hover:bg-amber-400"
              >
                Create a free account
                <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <Link
                to="/login"
                className="text-base font-medium text-slate-300 hover:text-white"
              >
                Sign in
              </Link>
            </div>
          </div>

          <figure className="max-w-sm lg:mt-10">
            <blockquote className="rounded-2xl rounded-bl-sm bg-white/[0.07] px-5 py-4 text-[15px] leading-7 text-slate-100 backdrop-blur-sm">
              Saw Sunday&apos;s long climb. Tomorrow stays easy for 45 minutes,
              and the threshold work moves to Thursday.
            </blockquote>
            <figcaption className="mt-2 pl-1 text-xs text-slate-400">
              Your coach, Monday morning
            </figcaption>
          </figure>
        </div>
      </section>

      <section className="mx-auto w-full max-w-6xl px-5 py-24 sm:px-8">
        <ul className="grid gap-12 md:grid-cols-3 md:gap-10">
          {moments.map(({ title, body }) => (
            <li key={title} className="border-t border-white/15 pt-6">
              <h2 className="text-lg font-semibold">{title}</h2>
              <p className="mt-3 text-[15px] leading-7 text-slate-400">
                {body}
              </p>
            </li>
          ))}
        </ul>
      </section>

      <footer className="border-t border-white/10">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-5 py-10 text-sm text-slate-400 sm:px-8 md:flex-row md:items-end md:justify-between">
          <div className="max-w-xl space-y-1.5">
            {facts.map((fact) => (
              <p key={fact}>{fact}</p>
            ))}
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            <a
              href={SETUP_GUIDE_URL}
              target="_blank"
              rel="noreferrer"
              className="text-slate-300 hover:text-white"
            >
              Setup guide
            </a>
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="text-slate-300 hover:text-white"
            >
              Source available, FSL-1.1-ALv2
            </a>
          </div>
        </div>
      </footer>
    </main>
  );
}
