import { useEffect, useRef, useState } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import { LayoutDashboard, Settings, Menu, X, Bike, SlidersHorizontal, CheckCircle, LogOut, User, LifeBuoy, ShieldCheck, Scale } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useImportProgress } from '../hooks/useImportProgress'
import { usePlanTranslationSync } from '../hooks/usePlanTranslations'
import { usePlanAutoRebuild } from '../hooks/usePlanAutoRebuild'
import { useLegalContact } from '../hooks/useLegalContact'
import { useOverlayDismiss } from '../hooks/useOverlayDismiss'
import { useAppStore } from '../store/useAppStore'
import { useProfile } from '../queries/profile'
import type { UserProfile } from '../store/useAppStore'
import { signOut } from '../services/sessions'
import { useT } from '../i18n'

const navItems: { to: string; label: string; icon: LucideIcon; exact?: boolean }[] = [
  { to: '/', label: 'Coach', icon: LayoutDashboard, exact: true },
  { to: '/settings', label: 'Settings', icon: Settings },
]

function Brand({ compact, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  return (
    <NavLink
      to="/"
      end
      onClick={onNavigate}
      className="flex items-center gap-2 rounded-lg focus:outline-hidden focus-visible:ring-2 focus-visible:ring-amber-400"
    >
      <div className={`bg-amber-500 rounded-lg ${compact ? 'p-1' : 'p-1.5'}`}>
        <Bike size={compact ? 18 : 20} className="text-white" />
      </div>
      <span className={`whitespace-nowrap font-bold tracking-tight ${compact ? 'text-base' : 'text-base'}`}>
        Train Like a Pro
      </span>
    </NavLink>
  )
}

function NavLinks({ onClick }: { onClick?: () => void }) {
  const t = useT()
  return (
    <nav className="flex flex-col gap-1 mt-6">
      {navItems.map(({ to, label, icon: Icon, exact }) => (
        <NavLink
          key={to}
          to={to}
          end={exact}
          onClick={onClick}
          className={({ isActive }) =>
            `flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors ${
              isActive
                ? 'bg-amber-500 text-gray-950'
                : 'text-gray-300 hover:bg-white/10 hover:text-white'
            }`
          }
        >
          <Icon size={18} />
          {t(label)}
        </NavLink>
      ))}
    </nav>
  )
}

function ExpertToggle({
  isExpertMode,
  toggleExpertMode,
}: {
  isExpertMode: boolean
  toggleExpertMode: () => void
}) {
  const t = useT()
  return (
    <button
      onClick={toggleExpertMode}
      title={isExpertMode ? t('Expert mode ON — click to turn off') : t('Turn on Expert mode')}
      className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors ${
        isExpertMode
          ? 'bg-amber-500 text-gray-950'
          : 'text-gray-300 hover:bg-white/10 hover:text-white'
      }`}
    >
      <SlidersHorizontal size={18} />
      {t('Expert')}
    </button>
  )
}

/** Where to turn when something is broken, and the privacy page (ai-trainer-ops#15, #2).
 *  The problem link appears only when the server names a contact address. */
function HelpLinks({ contactEmail, onNavigate }: { contactEmail: string | null; onNavigate?: () => void }) {
  const t = useT()
  const linkClass =
    'w-full flex items-center gap-3 px-4 py-2 rounded-lg text-sm text-gray-400 hover:bg-white/10 hover:text-white transition-colors'
  return (
    <div className="border-t border-white/10 pt-2 mt-2">
      {contactEmail && (
        <a
          href={`mailto:${contactEmail}?subject=${encodeURIComponent(t('Problem with Train Like a Pro'))}`}
          className={linkClass}
        >
          <LifeBuoy size={16} aria-hidden="true" />
          {t('Report a problem')}
        </a>
      )}
      <NavLink to="/privacy" onClick={onNavigate} className={linkClass}>
        <ShieldCheck size={16} aria-hidden="true" />
        {t('Privacy policy')}
      </NavLink>
      <NavLink to="/impressum" onClick={onNavigate} className={linkClass}>
        <Scale size={16} aria-hidden="true" />
        {t('Legal notice')}
      </NavLink>
    </div>
  )
}

function AccountBlock({ userProfile, onLogout }: { userProfile: UserProfile | null; onLogout: () => void }) {
  const t = useT()
  return (
    <div className="border-t border-white/10 pt-2 mt-2">
      {userProfile?.name && (
        <div className="flex items-center gap-2 px-4 py-2 text-sm text-gray-400">
          <User size={16} className="shrink-0" />
          <span className="truncate">{userProfile.name}</span>
        </div>
      )}
      <button
        onClick={onLogout}
        className="w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-gray-300 hover:bg-white/10 hover:text-white transition-colors"
      >
        <LogOut size={18} />
        {t('Sign out')}
      </button>
    </div>
  )
}

export default function Layout() {
  const t = useT()
  usePlanTranslationSync()
  // The plan follows the athlete's own changes, wherever they make them (ai-trainer-ops#206).
  usePlanAutoRebuild()
  const contactEmail = useLegalContact()?.contactEmail ?? null
  const [mobileOpen, setMobileOpen] = useState(false)
  const isExpertMode = useAppStore((s) => s.isExpertMode)
  const toggleExpertMode = useAppStore((s) => s.toggleExpertMode)
  const userProfile = useProfile()
  const logout = useAppStore((s) => s.logout)

  const importProgress = useImportProgress()
  const prevStatusRef = useRef(importProgress.status)
  const [toast, setToast] = useState<string | null>(null)
  const drawerRef = useRef<HTMLElement>(null)

  const handleLogout = () => signOut(logout)

  useEffect(() => {
    if (prevStatusRef.current === 'running' && importProgress.status === 'done') {
      const n = importProgress.imported
      // Counted as activities, not rides: runs and gym sessions are imported
      // too (ai-trainer-ops#121).
      const imported = n === 1 ? t('1 activity imported') : t('{n} activities imported', { n })
      const skipped = importProgress.skipped
      setToast(
        skipped > 0
          ? t('History imported: {imported}, {skipped} skipped', { imported, skipped })
          : t('History imported: {imported}', { imported })
      )
      const id = setTimeout(() => setToast(null), 5000)
      return () => clearTimeout(id)
    }
    prevStatusRef.current = importProgress.status
  }, [importProgress.status, importProgress.imported, importProgress.skipped, t])

  // Body-scroll lock, focus trap, Escape and focus restore — shared with the
  // dashboard's calendar overlay so both behave the same way (#621).
  useOverlayDismiss(mobileOpen, () => setMobileOpen(false), drawerRef)

  return (
    <div className="flex min-h-screen bg-gray-100">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex flex-col w-56 bg-[#0f1116] text-white px-3 py-5 fixed inset-y-0 left-0 z-30">
        <div className="px-2 mb-2">
          <Brand />
        </div>
        <NavLinks />
        <div className="mt-auto px-1 pt-4">
          <ExpertToggle isExpertMode={isExpertMode} toggleExpertMode={toggleExpertMode} />
          <HelpLinks contactEmail={contactEmail} />
          <AccountBlock userProfile={userProfile} onLogout={handleLogout} />
        </div>
      </aside>

      {/* Mobile top bar */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-40 bg-[#0f1116] text-white flex items-center justify-between px-4 h-14">
        <Brand compact onNavigate={() => setMobileOpen(false)} />
        <div className="flex items-center gap-1">
          <button
            onClick={toggleExpertMode}
            title={isExpertMode ? t('Expert mode ON') : t('Expert mode OFF')}
            className={`inline-flex min-h-10 min-w-10 items-center justify-center rounded-lg transition-colors ${
              isExpertMode ? 'bg-amber-500 text-gray-950' : 'text-gray-300 hover:bg-white/10'
            }`}
            aria-label={t('Toggle expert mode')}
          >
            <SlidersHorizontal size={18} />
          </button>
          <button
            onClick={() => setMobileOpen(!mobileOpen)}
            className="inline-flex min-h-10 min-w-10 items-center justify-center rounded-lg hover:bg-white/10"
            aria-label={t('Toggle menu')}
            aria-expanded={mobileOpen}
          >
            {mobileOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>
      </div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-30" onClick={() => setMobileOpen(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <aside
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label={t('Navigation menu')}
            className="absolute top-14 left-0 bottom-0 w-56 bg-[#0f1116] text-white px-3 py-4 flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <NavLinks onClick={() => setMobileOpen(false)} />
            <div className="mt-auto px-1 pt-4">
              <HelpLinks contactEmail={contactEmail} onNavigate={() => setMobileOpen(false)} />
              <AccountBlock userProfile={userProfile} onLogout={handleLogout} />
            </div>
          </aside>
        </div>
      )}

      {/* Main content. `min-w-0`: a flex item is otherwise as wide as its widest
          unbreakable line, so a long activity name pushed the page past a
          phone's width instead of being cut short by its `truncate`. */}
      <main className="flex-1 min-w-0 md:ml-56 pt-14 md:pt-0 min-h-screen">
        <div className="p-4 md:p-8 max-w-6xl mx-auto">
          <Outlet />
        </div>
      </main>

      {/* Import complete toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2 bg-gray-900 text-white text-sm font-medium px-4 py-3 rounded-xl shadow-lg animate-fade-in">
          <CheckCircle size={16} className="text-green-400 shrink-0" />
          {toast}
          <button
            onClick={() => setToast(null)}
            className="ml-2 text-gray-400 hover:text-white text-xs"
            aria-label={t('Dismiss')}
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  )
}
