import type { ChatMessage } from '../../store/useAppStore'
import { useT } from '../../i18n'
import { REASONING_SOURCE_META, REASONING_BADGE_CLASS } from '../../utils/reasoningSource'
import CoachMarkdown from './CoachMarkdown'
import ReplyConfirmations from '../ReplyConfirmations'
import ReportedSessionOffer from '../ReportedSessionOffer'
import RaceOffer from '../RaceOffer'
import CoachSaw from '../CoachSaw'
import DaySummaries from './DaySummaries'
import ReplyNotes from './ReplyNotes'
import WrongFlag from '../WrongFlag'
import { button, textButton } from '../ui'

// The athlete's words in a grey bubble on the right, the coach's as plain
// text on the left: the side says who is speaking, without avatars or an
// amber bubble (owner's review, 2026-10-24).
//
// Out of `AIChat` (ai-trainer-ops#274). The retry shows on the failure notice of the last
// message that failed.
export default function ChatMessageView({
  msg,
  lastFailedMessage,
  retryDisabled,
  onRetry,
}: {
  msg: ChatMessage
  lastFailedMessage: string | null
  retryDisabled: boolean
  onRetry: (failedUserMessage?: string) => void
}) {
  const t = useT()
  return (
    <div className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`text-sm ${
          msg.role === 'user'
            ? 'max-w-[80%] rounded-2xl rounded-br-sm bg-gray-100 px-3 py-2 text-gray-900'
            : 'max-w-[90%] text-gray-800'
        }`}
      >
        {msg.planUpdateCount ? (
          <>
            {msg.role === 'assistant' ? (
              <CoachMarkdown text={msg.content} />
            ) : (
              <p className="whitespace-pre-wrap">{msg.content}</p>
            )}
            <p className="mt-2 text-gray-500">
              {msg.planUpdateCount === 1
                ? t('Training plan updated: 1 day modified.')
                : t('Training plan updated: {count} days modified.', { count: msg.planUpdateCount })}
            </p>
          </>
        ) : msg.role === 'assistant' ? (
          <CoachMarkdown text={msg.content} />
        ) : (
          <span className="whitespace-pre-wrap">{msg.content}</span>
        )}
        {msg.role === 'assistant' && msg.daySummaries && <DaySummaries items={msg.daySummaries} />}
        {msg.role === 'assistant' && msg.notes && <ReplyNotes notes={msg.notes} />}
        {msg.role === 'assistant' && <ReplyConfirmations msg={msg} />}
        {msg.role === 'assistant' && msg.raceOffer && (
          <RaceOffer date={msg.raceOffer.date} replaces={msg.raceOffer.replaces} />
        )}
        {msg.role === 'assistant' && msg.reportedSession && (
          <ReportedSessionOffer session={msg.reportedSession} />
        )}
        {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
          <div className="mt-2 border-t border-gray-100 pt-2">
            <p className="mb-1 font-medium text-gray-700">{t('Sources')}</p>
            <ul className="space-y-0.5">
              {msg.sources.map((source, idx) => (
                <li key={idx} className="text-gray-500">
                  {source.url ? (
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline hover:text-gray-900"
                    >
                      {source.title}
                    </a>
                  ) : (
                    source.title
                  )}
                  {source.doi && (
                    <span className="ml-1 text-gray-400">· DOI: {source.doi}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {msg.role === 'assistant' &&
          (msg.physiologyRationale || msg.contextRationale || msg.objectiveRationale) && (
          <details className="mt-2 border-t border-gray-100 pt-2">
            <summary className={`cursor-pointer ${textButton}`}>{t('Why this answer?')}</summary>
            <dl className="mt-1 space-y-1">
              {/* Rationale layers map onto the shared knowledge sources (#377):
                  the physiology read is a coach inference, the personal-context
                  read is a personal observation. Cited science, when any, shows
                  in the Sources block above as the scientific-evidence source.

                  The objective link (#565) is deliberately NOT a fourth source
                  badge: the three above say where a claim came from, this says
                  what it is for. It leads the list because it is the part the
                  athlete came for — the rest explains how it was arrived at. */}
              {msg.objectiveRationale && (
                <div className="text-gray-600">
                  <dt className={`mr-1.5 inline ${REASONING_BADGE_CLASS}`}>{t('What this buys you')}</dt>
                  <dd className="inline">{msg.objectiveRationale}</dd>
                </div>
              )}
              {msg.physiologyRationale && (
                <div className="text-gray-600">
                  <dt className={`mr-1.5 inline ${REASONING_BADGE_CLASS}`}>
                    {t(REASONING_SOURCE_META.coach_inference.label)}
                  </dt>
                  <dd className="inline">{msg.physiologyRationale}</dd>
                </div>
              )}
              {msg.contextRationale && (
                <div className="text-gray-600">
                  <dt className={`mr-1.5 inline ${REASONING_BADGE_CLASS}`}>
                    {t(REASONING_SOURCE_META.personal_observation.label)}
                  </dt>
                  <dd className="inline">{msg.contextRationale}</dd>
                </div>
              )}
            </dl>
          </details>
        )}
        {msg.role === 'assistant' && msg.coachSaw && <CoachSaw items={msg.coachSaw} />}
        {/* Not on a failure notice: that is the app talking, not the coach. Not on
            the welcome either: it is the app's text, has no reference, and the
            server refused the flag (ai-trainer-ops#131). */}
        {msg.role === 'assistant' && !msg.failedUserMessage && msg.timestamp && (
          <WrongFlag target="reply" flagRef={msg.timestamp} excerpt={msg.content} />
        )}
        {msg.role === 'assistant' && msg.failedUserMessage && lastFailedMessage === msg.failedUserMessage && (
          <button
            onClick={() => onRetry(msg.failedUserMessage)}
            disabled={retryDisabled}
            aria-label={t('Retry coach response')}
            className={`mt-2 ${button}`}
          >
            {t('Retry')}
          </button>
        )}
      </div>
    </div>
  )
}
