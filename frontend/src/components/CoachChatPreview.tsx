import { Bike, Sparkles } from 'lucide-react'

const exchange = [
  {
    from: 'athlete' as const,
    text: 'Only 45 min tomorrow and the legs are still wrecked from Sunday. Skip the intervals?',
  },
  {
    from: 'coach' as const,
    text: "Skip them. You're nine days out and Sunday already banked the hard work for this week. 45 easy tomorrow, threshold moves to Thursday.",
  },
]

const chips = ['Target: Ötztaler, 6 Sep', 'Last hard day: Sunday', 'Plan updated']

export default function CoachChatPreview() {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4 shadow-2xl backdrop-blur sm:p-5">
      <div className="mb-4 flex items-center gap-2 border-b border-white/10 pb-3">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500 text-[#111318]">
          <Bike size={17} aria-hidden="true" />
        </span>
        <span className="text-sm font-semibold text-white">Your coach</span>
        <span className="ml-auto flex items-center gap-1 text-xs font-medium text-emerald-300">
          <Sparkles size={12} aria-hidden="true" />
          Always on
        </span>
      </div>

      <div className="space-y-3">
        {exchange.map(({ from, text }) => (
          <div key={text} className={from === 'athlete' ? 'flex justify-end' : 'flex justify-start'}>
            <p
              className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-6 ${
                from === 'athlete'
                  ? 'rounded-br-sm bg-amber-500 text-[#111318]'
                  : 'rounded-bl-sm bg-white/10 text-slate-100'
              }`}
            >
              {text}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap gap-2 border-t border-white/10 pt-3">
        {chips.map((chip) => (
          <span
            key={chip}
            className="rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-1 text-xs text-slate-300"
          >
            {chip}
          </span>
        ))}
      </div>
    </div>
  )
}
