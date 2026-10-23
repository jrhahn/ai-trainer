/**
 * The few styles the settings are built from (owner's review, 2026-10-23:
 * "einheitliche Schriftfarben, Font, Größen, Stil … weniger ist mehr").
 *
 * Two sizes in a card: the title (base, semibold) and everything else (sm):
 * text, labels, chips, inputs, buttons, notes. A second round of the review
 * found big chips next to many small lines.
 *
 * One accent, amber, for switches, chosen chips and links. Everything else is
 * grey: buttons, states and notes. Red is for errors, and for the last step
 * of a deletion; a delete button turns red only on hover.
 */
export const fieldLabel = 'block text-sm font-medium text-gray-700 mb-1.5'

export const input =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-amber-500 focus:ring-amber-500'

export const button =
  'inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50'

export const deleteButton = `${button} hover:border-red-300 hover:text-red-700`

export const textButton = 'text-sm font-medium text-gray-500 hover:text-gray-900 disabled:opacity-50'

/** A note under a control: grey when it worked, red when it did not. */
export const note = (ok: boolean) => `text-sm ${ok ? 'text-gray-500' : 'text-red-600'}`

/** A choice among a few: language, sports, days, provider. */
export const chip = (on: boolean) =>
  `rounded-full border px-3 py-1 text-sm transition-colors ${
    on ? 'border-amber-500 bg-amber-50 text-amber-900' : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300'
  }`
