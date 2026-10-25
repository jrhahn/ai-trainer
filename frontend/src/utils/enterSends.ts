/** On a phone keyboard Enter is the only way to a second line, so there it makes
 *  one and the send button sends (ai-trainer-ops#270). */
export function enterSends(): boolean {
  return !window.matchMedia?.('(pointer: coarse)').matches
}
