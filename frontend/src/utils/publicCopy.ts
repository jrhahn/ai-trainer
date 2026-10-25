import { Gift, MessageCircle, RefreshCw } from 'lucide-react'

// The copy of the public pages, out of their components so the copy tests
// can read it while a component file exports only components (fast refresh,
// ai-trainer-ops#286).

// The three lines beside the sign-in forms (`AuthShell`). No Gemini key and no
// Strava as a source (ai-trainer-ops#218): testers use the shared key (#9), and
// rides that reach intervals.icu only from Strava are not passed on.
export const highlights = [
  { label: 'Coaching built on your season', icon: MessageCircle },
  { label: 'Rides come in through intervals.icu', icon: RefreshCw },
  { label: 'Free, no subscription', icon: Gift },
]

// The landing page's moments and facts, read by the copy-voice guard in
// LandingPage.test.tsx (#610). Three short
// moments, a sentence or two each, with no cards or icons around them: the page
// should feel like the coach, quiet and attentive, rather than like a feature
// grid (ai-trainer-ops#67).
export const moments = [
  {
    title: 'Your coach already knows',
    body: 'Every ride you sync shapes the next session, so the week fits how the last one actually went.',
  },
  {
    title: 'Your coach asks only what matters',
    body: 'A question comes when the answer would change your plan. Otherwise your coach simply gets on with the plan.',
  },
  {
    title: 'Talk to your coach whenever you like',
    body: 'Mention that the legs are flat or ask what to eat before a long day. The plan moves with you.',
  },
]

export const facts = [
  'Free to use. Coaching runs on our Google Gemini key up to an allowance per 30 days. With your own key there is no limit.',
  'Rides come from your device through intervals.icu. Strava-only rides do not reach us. You can also upload a FIT file or enter your sessions yourself.',
]
