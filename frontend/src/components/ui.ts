/**
 * The few styles the pages are built from (owner's review, 2026-10-23:
 * "einheitliche Schriftfarben, Font, Größen, Stil … weniger ist mehr").
 * Settings came first. On 2026-10-24 the owner asked for every other page
 * "genauso", the coach page first.
 *
 * Two sizes in a card: the title (base, semibold) and everything else (sm):
 * text, labels, chips, inputs, buttons, notes. A second round of the review
 * found big chips next to many small lines.
 *
 * One accent, amber, for switches, chosen chips and links. Everything else is
 * grey: buttons, states and notes. Red is for errors, and for the last step
 * of a deletion; a delete button turns red only on hover. No icons beside a
 * title or a label, and no tinted boxes or coloured badges.
 */
/** A card without its padding, for one that lays out its own (the chat). */
export const cardFrame = 'rounded-2xl border border-gray-100 bg-white shadow-xs'

export const card = `${cardFrame} p-5`

/** A card's title, and a section's heading where there is no card. */
export const cardTitle = 'text-base font-semibold text-gray-900'

/** A label that wraps its field, and the same with room under it for a field that follows. */
export const formLabel = 'block text-sm font-medium text-gray-700'

export const fieldLabel = `${formLabel} mb-1.5`

/** A unit inside the right end of an input ("km", "m"); the input needs `pr-10`. */
export const unit = 'pointer-events-none absolute right-3 top-2 text-sm text-gray-500'

export const input =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-amber-500 focus:ring-amber-500'

export const button =
  'inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50'

export const deleteButton = `${button} hover:border-red-300 hover:text-red-700`

export const textButton = 'text-sm font-medium text-gray-500 hover:text-gray-900 disabled:opacity-50'

/** A button that is only an icon: edit, close. It needs an aria-label. 40 px
 *  to hit on a phone (ai-trainer-ops#131). */
const iconButtonBase =
  'inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 disabled:opacity-50'

export const iconButton = `${iconButtonBase} hover:text-gray-700`

/** The same for a delete: red on hover only, like `deleteButton`. */
export const iconDeleteButton = `${iconButtonBase} hover:text-red-600`

/** Items in one line with a dot between them, drawn by CSS: "Endurance · 1 h ·
 *  250 W". Each item stays its own element, for screen readers and tests. */
export const dotted = "before:mx-1.5 before:content-['·'] first:before:content-none"

/** A note under a control: grey when it worked, red when it did not. */
export const note = (ok: boolean) => `text-sm ${ok ? 'text-gray-500' : 'text-red-600'}`

/** A choice among a few: language, sports, days, provider. */
export const chip = (on: boolean) =>
  `rounded-full border px-3 py-1 text-sm transition-colors ${
    on ? 'border-amber-500 bg-amber-50 text-amber-900' : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300'
  }`
