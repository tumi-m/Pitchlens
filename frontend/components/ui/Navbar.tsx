'use client';
import { useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { LayoutDashboard, Upload, LogOut, Moon, Sun, Menu, X } from 'lucide-react';
import { useAuthContext } from '@/components/auth/AuthProvider';
import { useTheme } from '@/components/ui/ThemeProvider';
import { AuthModal } from '@/components/auth/AuthModal';
import { logout } from '@/lib/firebase/auth';
import { cn } from '@/lib/utils/cn';
import toast from 'react-hot-toast';

const navLinks = [
  { href: '/upload', label: 'Upload', icon: Upload },
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
];

export function Navbar() {
  const { user } = useAuthContext();
  const { theme, toggleTheme } = useTheme();
  const pathname = usePathname();
  const router = useRouter();
  const [authOpen, setAuthOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    toast.success('Signed out successfully');
    router.push('/');
  };

  return (
    <>
      <header className="fixed top-0 left-0 right-0 z-30 border-b border-pitch-indigo-soft/20 bg-pitch-black/80 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5 group">
            <div className="w-8 h-8 bg-pitch-green/20 border border-pitch-green/40 rounded-lg
              flex items-center justify-center group-hover:bg-pitch-green/30 transition-colors">
              <span className="text-sm">⚽</span>
            </div>
            <span className="font-bold text-lg tracking-tight text-pitch-white">Pitchlens</span>
          </Link>

          {/* Desktop nav */}
          <nav className="hidden md:flex items-center gap-1">
            {user && navLinks.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                className={cn(
                  'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all',
                  pathname.startsWith(href)
                    ? 'bg-pitch-indigo-soft/30 text-pitch-white'
                    : 'text-pitch-muted hover:text-pitch-white hover:bg-pitch-indigo-deep'
                )}
              >
                <Icon size={16} />
                {label}
              </Link>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            <button
              onClick={toggleTheme}
              className="p-2 rounded-lg text-pitch-muted hover:text-pitch-white hover:bg-pitch-indigo-deep transition-all"
              aria-label="Toggle theme"
            >
              {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            </button>

            {user ? (
              <div className="hidden md:flex items-center gap-2">
                <div className="w-8 h-8 rounded-full bg-pitch-indigo-soft flex items-center justify-center text-xs font-bold text-pitch-white overflow-hidden">
                  {user.photoURL
                    ? <img src={user.photoURL} alt="avatar" className="w-full h-full object-cover" />
                    : (user.email?.[0] ?? 'U').toUpperCase()}
                </div>
                <button onClick={handleLogout} className="pitch-button-ghost text-sm">
                  <LogOut size={16} />
                </button>
              </div>
            ) : (
              <button onClick={() => setAuthOpen(true)} className="pitch-button-primary hidden md:flex text-sm px-4 py-2">
                Sign In
              </button>
            )}

            <button
              className="md:hidden p-2 text-pitch-muted hover:text-pitch-white"
              onClick={() => setMobileOpen(!mobileOpen)}
              aria-label="Toggle mobile menu"
            >
              {mobileOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </div>

        {/* Mobile menu */}
        {mobileOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="md:hidden border-t border-pitch-indigo-soft/20 bg-pitch-black/95 px-4 py-3 space-y-1"
          >
            {user && navLinks.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                onClick={() => setMobileOpen(false)}
                className="flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm text-pitch-muted hover:text-pitch-white hover:bg-pitch-indigo-deep transition-all"
              >
                <Icon size={16} />
                {label}
              </Link>
            ))}
            {user
              ? <button onClick={handleLogout} className="flex items-center gap-2 px-3 py-2.5 text-sm text-pitch-muted hover:text-pitch-white w-full">
                  <LogOut size={16} /> Sign Out
                </button>
              : <button onClick={() => { setAuthOpen(true); setMobileOpen(false); }} className="pitch-button-primary w-full mt-2">
                  Sign In
                </button>
            }
          </motion.div>
        )}
      </header>

      <AuthModal isOpen={authOpen} onClose={() => setAuthOpen(false)} />
    </>
  );
}
