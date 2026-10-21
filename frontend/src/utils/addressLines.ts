/** "Street 1, 12345 Town" as the lines of a letter's address (ai-trainer-ops#1). */
export function addressLines(address: string | null | undefined): string[] {
  return (address ?? '')
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}
