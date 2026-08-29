import { useEffect, useRef, useState } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import { LayoutDashboard, Settings, Menu, X, Bike, SlidersHorizontal, CheckCircle, LogOut, User } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useImportProgress } from '../hooks/useImportProgress'
import { useOverlayDismiss } from '../hooks/useOverlayDismiss'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'
import { AUTHELIA_URL } from '../services/api'

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
      className="flex items-center gap-2 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
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
                ? 'bg-amber-500 text-white'
                : 'text-gray-300 hover:bg-white/10 hover:text-white'
            }`
          }
        >
          <Icon size={18} />
          {label}
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
  return (
    <button
      onClick={toggleExpertMode}
      title={isExpertMode ? 'Expert mode ON — click to turn off' : 'Turn on Expert mode'}
      className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors ${
        isExpertMode
          ? 'bg-amber-500 text-white'
          : 'text-gray-300 hover:bg-white/10 hover:text-white'
      }`}
    >
      <SlidersHorizontal size={18} />
      Expert
    </button>
  )
}

function AccountBlock({ userProfile, onLogout }: { userProfile: UserProfile | null; onLogout: () => void }) {
  return (
    <div className="border-t border-white/10 pt-2 mt-2">
      {userProfile?.name && (
        <div className="flex items-center gap-2 px-4 py-2 text-sm text-gray-400">
          <User size={16} className="flex-shrink-0" />
          <span className="truncate">{userProfile.name}</span>
        </div>
      )}
      <button
        onClick={onLogout}
        className="w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-gray-300 hover:bg-white/10 hover:text-white transition-colors"
      >
        <LogOut size={18} />
        Sign out
      </button>
    </div>
  )
}

export default function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const isExpertMode = useAppStore((s) => s.isExpertMode)
  const toggleExpertMode = useAppStore((s) => s.toggleExpertMode)
  const userProfile = useAppStore((s) => s.userProfile)
  const logout = useAppStore((s) => s.logout)

  const importProgress = useImportProgress()
  const prevStatusRef = useRef(importProgress.status)
  const [toast, setToast] = useState<string | null>(null)
  const drawerRef = useRef<HTMLElement>(null)

  const handleLogout = () => {
    logout()
    if (AUTHELIA_URL) {
      window.location.href = `${AUTHELIA_URL}/logout`
    }
  }

  useEffect(() => {
    if (prevStatusRef.current === 'running' && importProgress.status === 'done') {
      const n = importProgress.imported
      setToast(
        `Ride history imported — ${n} ride${n !== 1 ? 's' : ''} imported` +
          (importProgress.skipped > 0 ? `, ${importProgress.skipped} skipped` : '')
      )
      const id = setTimeout(() => setToast(null), 5000)
      return () => clearTimeout(id)
    }
    prevStatusRef.current = importProgress.status
  }, [importProgress.status, importProgress.imported, importProgress.skipped])

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
          <AccountBlock userProfile={userProfile} onLogout={handleLogout} />
        </div>
      </aside>

      {/* Mobile top bar */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-40 bg-[#0f1116] text-white flex items-center justify-between px-4 h-14">
        <Brand compact onNavigate={() => setMobileOpen(false)} />
        <div className="flex items-center gap-1">
          <button
            onClick={toggleExpertMode}
            title={isExpertMode ? 'Expert mode ON' : 'Expert mode OFF'}
            className={`p-2 rounded-lg transition-colors ${
              isExpertMode ? 'bg-amber-500 text-white' : 'text-gray-300 hover:bg-white/10'
            }`}
            aria-label="Toggle expert mode"
          >
            <SlidersHorizontal size={18} />
          </button>
          <button
            onClick={() => setMobileOpen(!mobileOpen)}
            className="p-2 rounded-lg hover:bg-white/10"
            aria-label="Toggle menu"
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
            aria-label="Navigation menu"
            className="absolute top-14 left-0 bottom-0 w-56 bg-[#0f1116] text-white px-3 py-4 flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <NavLinks onClick={() => setMobileOpen(false)} />
            <div className="mt-auto px-1 pt-4">
              <AccountBlock userProfile={userProfile} onLogout={handleLogout} />
            </div>
          </aside>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 md:ml-56 pt-14 md:pt-0 min-h-screen">
        <div className="p-4 md:p-8 max-w-6xl mx-auto">
          <Outlet />
        </div>
      </main>

      {/* Import complete toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2 bg-gray-900 text-white text-sm font-medium px-4 py-3 rounded-xl shadow-lg animate-fade-in">
          <CheckCircle size={16} className="text-green-400 flex-shrink-0" />
          {toast}
          <button
            onClick={() => setToast(null)}
            className="ml-2 text-gray-400 hover:text-white text-xs"
            aria-label="Dismiss"
          >✕</button>
        </div>
      )}
    </div>
  )
}
