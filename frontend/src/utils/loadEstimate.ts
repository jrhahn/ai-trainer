import type { FitUploadFileResult } from '../services/user'

/**
 * What an imported file's load was estimated from, or `null` when a device
 * measured it (ai-trainer-ops#311). A GPX or TCX carries no device summary, and
 * a .fit may carry no power: the owner wanted the athlete told, not a guess
 * booked as a measurement.
 */
export function loadEstimate(file: FitUploadFileResult): string | null {
  if (file.status !== 'imported') return null
  switch (file.loadSource) {
    case 'heart_rate':
      return 'Load estimated from your heart rate: the file has no power.'
    case 'rpe':
      return 'Load estimated from the effort you logged for that day.'
    case 'duration':
      // Heart rate in the file and still the duration: the profile has no
      // maximum to read it against.
      return file.averageHeartRate
        ? 'Load estimated from the duration only. With your maximum heart rate in your profile, it would come from your heart rate.'
        : 'Load estimated from the duration only: the file has neither power nor heart rate.'
    default:
      return null
  }
}
