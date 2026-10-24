import { useRef, useState } from 'react'
import { useAppStore } from '../store/useAppStore'
import { fetchDayBoard } from '../services/user'
import type { DayBoard } from '../services/user'
import { GITHUB_URL } from '../utils/links'
import { useT } from '../i18n'
import { card, cardTitle } from './ui'

// The "why" under today's session (ai-trainer-ops#17, #39 Q9): the day board
// the planner got, with each deduction by name, and a link to the code that
// computed it. Loaded on open, so a closed panel costs no request. Scores and
// a link to the code are the coach's working state, so the dashboard shows it
// in expert mode only (owner's review, 2026-10-24: "weniger ist mehr").
export const DAY_BOARD_TITLE = 'Which kind of day suits you today'

export default function DayBoardWhy() {
  const t = useT()
  const authToken = useAppStore((s) => s.authToken)
  const [board, setBoard] = useState<DayBoard | null>(null)
  const [failed, setFailed] = useState(false)
  const [open, setOpen] = useState(false)
  // One request at a time: closing and reopening while it runs must not send another.
  const loading = useRef(false)

  const load = async () => {
    if (board || !authToken || loading.current) return
    loading.current = true
    setFailed(false)
    try {
      setBoard(await fetchDayBoard(authToken))
    } catch {
      setFailed(true)
    } finally {
      loading.current = false
    }
  }

  // ref is the deployed commit, or develop when the backend does not know it.
  const sourceUrl = board
    ? `${GITHUB_URL}/blob/${board.source.ref}/${board.source.path}#L${board.source.line}`
    : ''

  return (
    // A button rather than <details>: the dashboard's one <details> is "More".
    <section className={`${card} text-sm text-gray-700`}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen(!open)
          if (!open) void load()
        }}
        className={`${cardTitle} text-left`}
      >
        {/* Not "What the planner weighs for a day": planner jargon a tester
            could not read (ai-trainer-ops#90). */}
        {t(DAY_BOARD_TITLE)}
      </button>
      {open && failed && <p className="mt-2 text-red-600">{t('Could not load it. Try again later.')}</p>}
      {open && board && board.options.length === 0 && (
        <p className="mt-2 text-gray-500">
          {t('Nothing to weigh yet: the coach does not know yet what you train for. Tell it in the chat.')}
        </p>
      )}
      {open && board && board.options.length > 0 && (
        <>
          <p className="mt-2 text-gray-500">
            {board.hot
              ? t('Each kind of day, scored on what you said matters to you, minus what it costs in the heat and in freshness you want for something else. The coach still places the sessions: your calendar, races and rest days come first.')
              : t('Each kind of day, scored on what you said matters to you, minus what it costs in freshness you want for something else. The coach still places the sessions: your calendar, races and rest days come first.')}
          </p>
          <ol className="mt-2 space-y-1">
            {board.options.map((option) => (
              <li key={option.key}>
                {/* Every word through t(): the line was English in a German interface (ai-trainer-ops#222). */}
                <span className="font-medium text-gray-900">{t(option.label)}</span>{' '}
                <span className="text-gray-500">
                  {t('score {score} (value {value}', { score: option.score, value: option.base })}
                  {option.heatCost > 0 && t(', −{n} heat', { n: option.heatCost })}
                  {option.freshnessCost > 0 &&
                    t(', −{n} freshness for {what}', { n: option.freshnessCost, what: t(option.competesWithLabel) })}
                  )
                </span>
              </li>
            ))}
          </ol>
          {board.hot && !board.heatTolerance && (
            <p className="mt-2 text-gray-500">
              {t('A hot day is coming, but your heat tolerance is not measured yet, so nothing is deducted for heat.')}
            </p>
          )}
          <a className="mt-2 inline-block text-amber-700 underline" href={sourceUrl} target="_blank" rel="noreferrer">
            {t('How this is computed: {symbol} in {file}', {
              symbol: board.source.symbol,
              file: board.source.path.replace(/.*\//, ''),
            })}
          </a>
        </>
      )}
    </section>
  )
}
