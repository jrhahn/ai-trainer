import { useState } from 'react'
import { useAppStore } from '../../store/useAppStore'
import { useAuthToken } from '../../hooks/useAuthToken'
import { deleteCurrentUser, exportAccountData } from '../../services/user'
import { slots, useT } from '../../i18n'
import { button, deleteButton, fieldLabel, input, textButton } from '../ui'

// The export, under "Your data", and deleting the account, at the end of the
// Account card (ai-trainer-ops#274: out of `SettingsPage`; #312).

/** The export card's anchor: the deletion links to it (ai-trainer-ops#6, #312). */
export const EXPORT_ANCHOR = 'settings-export'

/** Everything stored about the account, as one JSON file (ai-trainer-ops#6). */
export function ExportCard() {
  const t = useT()
  const authToken = useAuthToken()
  const [exportError, setExportError] = useState('')

  const handleExportAccount = async () => {
    if (!authToken) return
    setExportError('')
    try {
      const data = await exportAccountData(authToken)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `ai-trainer-export-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      setExportError(t('Export failed. Please try again.'))
    }
  }

  return (
    <div
      id={EXPORT_ANCHOR}
      // Clears the fixed mobile header and the sticky tabs on a jump, as a section does.
      className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6 scroll-mt-28 md:scroll-mt-16 lg:scroll-mt-8"
    >
      {/* "Your Data" twice, as section and card, said it once too often. */}
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Export')}</h3>
      <p className="text-sm text-gray-500 mb-4">
        {t('Download everything stored about your account as one JSON file. Passwords, keys and access tokens are not included.')}
      </p>
      <button onClick={handleExportAccount} className={button}>
        {t('Export My Data')}
      </button>
      {exportError && <p className="text-sm text-red-600 mt-2">{exportError}</p>}
    </div>

  )
}

/**
 * Deleting the account, behind its password (ai-trainer-ops#35, #131), at the
 * end of the Account card (#312).
 *
 * It was a "Danger Zone" that said "Permanently delete all your data and start
 * over from scratch" above a "Reset All Data" button, and only the step after
 * named the account: someone who wanted the account gone did not press
 * "Reset", and someone who wanted to start over deleted the account. The
 * export it sat under is a link now (#6).
 */
export function DeleteAccount() {
  const t = useT()
  const authToken = useAuthToken()
  const resetAll = useAppStore((s) => s.resetAll)
  // The account deletion asks for the password in the page, not in a native
  // prompt, which showed it in clear text (ai-trainer-ops#131).
  const [deleteConfirming, setDeleteConfirming] = useState(false)
  const [deletePassword, setDeletePassword] = useState('')
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  // No native confirm before it (#312): the password step in the page is the
  // confirmation, and the backend requires the password since #35.
  const startDelete = () => {
    if (!authToken) return
    setDeleteError(null)
    setDeleteConfirming(true)
  }

  const cancelDelete = () => {
    setDeleteConfirming(false)
    setDeletePassword('')
    setDeleteError(null)
  }

  const confirmDelete = async () => {
    if (!authToken || !deletePassword) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteCurrentUser(authToken, deletePassword)
    } catch (e) {
      // A wrong password keeps the session: the server answers 403, not 401.
      setDeleteError(t(e instanceof Error ? e.message : 'The account could not be deleted.'))
      setDeleting(false)
      return
    }
    resetAll()
  }

  return (
    <div className="mt-6 border-t border-gray-100 pt-4">
      <p className="text-sm text-gray-500 mb-4">
        {slots(t('Deletes your account and everything stored about it. It cannot be undone. {link} if you want a copy.'), {
          link: (
            <a key="link" href={`#${EXPORT_ANCHOR}`} className="font-medium text-amber-600 hover:underline">
              {t('Export your data first')}
            </a>
          ),
        })}
      </p>
      {!deleteConfirming && (
        <button onClick={startDelete} className={deleteButton}>
          {t('Delete account…')}
        </button>
      )}
      {deleteConfirming && (
        <form
          className="border border-gray-200 rounded-lg p-3 space-y-2"
          onSubmit={(e) => {
            e.preventDefault()
            void confirmDelete()
          }}
        >
          <label htmlFor="delete-account-password" className={fieldLabel}>
            {t('Enter your password to confirm.')}
          </label>
          <input
            id="delete-account-password"
            type="password"
            value={deletePassword}
            onChange={(e) => setDeletePassword(e.target.value)}
            className={input}
            autoComplete="current-password"
            autoFocus
          />
          {deleteError && <p className="text-sm text-red-600">{deleteError}</p>}
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={deleting || !deletePassword}
              // The one red button: the step that cannot be undone.
              className="inline-flex items-center rounded-lg border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              {t('Delete my account')}
            </button>
            <button type="button" onClick={cancelDelete} className={`${textButton} px-2`}>
              {t('Cancel')}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
