import type { TimelineEvent } from '../../utils/coachTimeline'
import { dotted } from '../ui'

/** A plan change or a recommendation in the coach timeline (#418).
 *  Out of `AIChat` (ai-trainer-ops#274). */
export default function TimelineEventView({ event }: { event: TimelineEvent }) {
  // A plan change is a log line, not a message: "PLAN GENERATION · 14 days
  // updated" used to weigh as much as the coach and was the first thing a
  // new account read (ai-trainer-ops#91). One muted line; the days on request.
  if (event.kind === 'plan-update') {
    const line = (
      <span>
        <span className={`font-medium ${dotted}`}>{event.title}</span>
        <span className={dotted}>{event.body}</span>
      </span>
    )
    return event.details?.length ? (
      <details
        data-testid="timeline-event"
        data-event-kind={event.kind}
        className="text-sm text-gray-500"
      >
        <summary className="cursor-pointer list-none hover:text-gray-900">{line}</summary>
        <ul className="mt-1 ml-4 space-y-0.5">
          {event.details.map((detail, i) => (
            <li key={i}>{detail}</li>
          ))}
        </ul>
      </details>
    ) : (
      <p data-testid="timeline-event" data-event-kind={event.kind} className="text-sm text-gray-500">
        {line}
      </p>
    )
  }
  // A recommendation: its title and its text, without the coloured icon and
  // the grey box (owner's review, 2026-10-24).
  return (
    <div data-testid="timeline-event" data-event-kind={event.kind} className="text-sm">
      <p className="font-medium text-gray-900">{event.title}</p>
      <p className="text-gray-700">{event.body}</p>
    </div>
  )
}
