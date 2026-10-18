import { useEffect, useState } from 'react'
import { apiFetch } from '../services/api'

/** Who runs this deployment, from its configuration (ai-trainer-ops#2, #15). */
export interface LegalContact {
  operatorName: string | null
  contactEmail: string | null
}

/** Reads the controller and contact once; null until known, and on failure. */
export function useLegalContact(): LegalContact | null {
  const [contact, setContact] = useState<LegalContact | null>(null)
  useEffect(() => {
    let live = true
    apiFetch<LegalContact>('/auth/legal')
      .then((value) => {
        if (live) setContact(value)
      })
      .catch((error) => console.warn('[Privacy] could not read the contact', error))
    return () => {
      live = false
    }
  }, [])
  return contact
}
