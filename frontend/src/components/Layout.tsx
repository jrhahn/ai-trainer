import { useState } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import { LayoutDashboard, Settings, Menu, X, Bike, FlaskConical } from 'lucide-react'

const navItems = [
  { to: '/', label: 'Coach', icon: LayoutDashboard, exact: true },
  { to: '/expert', label: 'Expert', icon: FlaskConical },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export default function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false)

  const NavLinks = ({ onClick }: { onClick?: () => void }) => (
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

  return (
    <div className="flex min-h-screen bg-gray-100">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex flex-col w-56 bg-[#1a1a2e] text-white px-3 py-6 fixed inset-y-0 left-0 z-30">
        <div className="flex items-center gap-2 px-4 mb-2">
          <div className="bg-amber-500 rounded-lg p-1.5">
            <Bike size={20} className="text-white" />
          </div>
          <span className="font-bold text-lg tracking-tight">Train Like a Pro!</span>
        </div>
        <NavLinks />
      </aside>

      {/* Mobile top bar */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-40 bg-[#1a1a2e] text-white flex items-center justify-between px-4 h-14">
        <div className="flex items-center gap-2">
          <div className="bg-amber-500 rounded-lg p-1">
            <Bike size={18} className="text-white" />
          </div>
          <span className="font-bold text-base tracking-tight">Train Like a Pro!</span>
        </div>
        <button
          onClick={() => setMobileOpen(!mobileOpen)}
          className="p-2 rounded-lg hover:bg-white/10"
          aria-label="Toggle menu"
        >
          {mobileOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-30" onClick={() => setMobileOpen(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <aside
            className="absolute top-14 left-0 bottom-0 w-56 bg-[#1a1a2e] text-white px-3 py-4"
            onClick={(e) => e.stopPropagation()}
          >
            <NavLinks onClick={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 md:ml-56 pt-14 md:pt-0 min-h-screen">
        <div className="p-4 md:p-8 max-w-6xl mx-auto">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
