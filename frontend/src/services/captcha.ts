/**
 * Solving the registration proof-of-work (#686).
 *
 * The server hands out `sha256(salt + number)` and the client finds `number`
 * by trying every value from zero. Brute force is the mechanism, not a flaw:
 * the cost is what a bot has to pay, and the real barrier is that obtaining a
 * solution requires executing this loop at all — a script POSTing the bare
 * registration form cannot.
 *
 * Runs on `crypto.subtle`, so no hashing library is bundled and the CSP needs
 * no change. That API is only available in a secure context, which production
 * (HTTPS) and local development (localhost) both are.
 */

export interface CaptchaChallenge {
  algorithm: string
  challenge: string
  salt: string
  signature: string
  maxnumber: number
}

export interface CaptchaSolution {
  challenge: string
  salt: string
  signature: string
  number: number
}

/** Yield to the event loop this often, in candidates tried. */
const YIELD_EVERY = 500

const encoder = new TextEncoder()

async function sha256Hex(input: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', encoder.encode(input))
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * Find the number behind `challenge`, or throw if there isn't one in range.
 *
 * The loop yields periodically so a slow device keeps painting and stays
 * responsive rather than appearing to hang — this runs while the user is
 * looking at the form, so jank here is the whole visible cost of the feature.
 */
export async function solveCaptcha(challenge: CaptchaChallenge): Promise<CaptchaSolution> {
  if (challenge.algorithm !== 'SHA-256') {
    throw new Error(`Unsupported captcha algorithm: ${challenge.algorithm}`)
  }

  for (let number = 0; number <= challenge.maxnumber; number++) {
    if (number > 0 && number % YIELD_EVERY === 0) {
      await new Promise((resolve) => setTimeout(resolve, 0))
    }
    const hex = await sha256Hex(`${challenge.salt}${number}`)
    if (hex === challenge.challenge) {
      return {
        challenge: challenge.challenge,
        salt: challenge.salt,
        signature: challenge.signature,
        number,
      }
    }
  }

  // Only reachable if the server and client disagree about the scheme, which
  // is worth surfacing as an error rather than submitting a form that is
  // guaranteed to be rejected.
  throw new Error('Could not solve the captcha challenge')
}
