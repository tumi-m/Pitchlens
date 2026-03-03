'use client';
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Mail, Lock, User, Loader2 } from 'lucide-react';
import { signInWithGoogle, signInWithEmail, registerWithEmail } from '@/lib/firebase/auth';
import { cn } from '@/lib/utils/cn';
import toast from 'react-hot-toast';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  defaultMode?: 'signin' | 'register';
}

export function AuthModal({ isOpen, onClose, defaultMode = 'signin' }: AuthModalProps) {
  const [mode, setMode] = useState(defaultMode);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);

  const handleGoogleSignIn = async () => {
    setLoading(true);
    try {
      await signInWithGoogle();
      onClose();
      toast.success('Welcome to Pitchlens!');
    } catch (err: any) {
      toast.error(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  const handleEmailSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      if (mode === 'signin') {
        await signInWithEmail(email, password);
        toast.success('Welcome back!');
      } else {
        await registerWithEmail(email, password);
        toast.success('Account created! Welcome to Pitchlens.');
      }
      onClose();
    } catch (err: any) {
      toast.error(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            className="fixed inset-0 bg-black/70 backdrop-blur-sm z-40"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.div
            className="fixed inset-0 flex items-center justify-center z-50 p-4"
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ type: 'spring', damping: 25, stiffness: 300 }}
          >
            <div className="glass-card w-full max-w-md p-8 relative">
              <button
                onClick={onClose}
                className="absolute top-4 right-4 text-pitch-muted hover:text-pitch-white transition-colors"
                aria-label="Close modal"
              >
                <X size={20} />
              </button>

              <div className="mb-8">
                <div className="w-10 h-10 bg-pitch-green/20 rounded-xl flex items-center justify-center mb-4">
                  <span className="text-2xl">⚽</span>
                </div>
                <h2 className="text-2xl font-bold text-pitch-white">
                  {mode === 'signin' ? 'Welcome back' : 'Join Pitchlens'}
                </h2>
                <p className="text-pitch-muted text-sm mt-1">
                  {mode === 'signin'
                    ? 'Sign in to access your match analytics'
                    : 'Start unveiling the geometry of your game'}
                </p>
              </div>

              <button
                onClick={handleGoogleSignIn}
                disabled={loading}
                className="w-full flex items-center justify-center gap-3 px-4 py-3 rounded-xl
                  bg-white/10 border border-white/20 hover:bg-white/15 transition-all
                  text-pitch-white font-medium text-sm mb-6 disabled:opacity-50"
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24">
                  <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                  <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                  <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                  <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                </svg>
                Continue with Google
              </button>

              <div className="flex items-center gap-3 mb-6">
                <div className="flex-1 h-px bg-pitch-indigo-soft/30" />
                <span className="text-pitch-muted text-xs">or continue with email</span>
                <div className="flex-1 h-px bg-pitch-indigo-soft/30" />
              </div>

              <form onSubmit={handleEmailSubmit} className="space-y-4">
                {mode === 'register' && (
                  <div className="relative">
                    <User size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-pitch-muted" />
                    <input
                      type="text"
                      placeholder="Full name"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      className="w-full pl-10 pr-4 py-3 bg-pitch-indigo-deep/50 border border-pitch-indigo-soft/30
                        rounded-xl text-pitch-white placeholder:text-pitch-muted text-sm focus:outline-none
                        focus:border-pitch-green transition-colors"
                    />
                  </div>
                )}
                <div className="relative">
                  <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-pitch-muted" />
                  <input
                    type="email"
                    placeholder="Email address"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    className="w-full pl-10 pr-4 py-3 bg-pitch-indigo-deep/50 border border-pitch-indigo-soft/30
                      rounded-xl text-pitch-white placeholder:text-pitch-muted text-sm focus:outline-none
                      focus:border-pitch-green transition-colors"
                  />
                </div>
                <div className="relative">
                  <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-pitch-muted" />
                  <input
                    type="password"
                    placeholder="Password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    minLength={6}
                    className="w-full pl-10 pr-4 py-3 bg-pitch-indigo-deep/50 border border-pitch-indigo-soft/30
                      rounded-xl text-pitch-white placeholder:text-pitch-muted text-sm focus:outline-none
                      focus:border-pitch-green transition-colors"
                  />
                </div>
                <button type="submit" disabled={loading} className="pitch-button-primary w-full py-3">
                  {loading ? <Loader2 size={16} className="animate-spin" /> : null}
                  {mode === 'signin' ? 'Sign In' : 'Create Account'}
                </button>
              </form>

              <p className="text-center text-pitch-muted text-sm mt-6">
                {mode === 'signin' ? "Don't have an account? " : 'Already have an account? '}
                <button
                  onClick={() => setMode(mode === 'signin' ? 'register' : 'signin')}
                  className="text-pitch-green hover:text-pitch-green-light transition-colors font-medium"
                >
                  {mode === 'signin' ? 'Sign up' : 'Sign in'}
                </button>
              </p>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
