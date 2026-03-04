'use client';
import { createContext, useContext, ReactNode } from 'react';
import { User } from 'firebase/auth';

interface AuthContextValue {
  user: User | null;
  loading: boolean;
}

const AuthContext = createContext<AuthContextValue>({ user: null, loading: false });

// Auth is bypassed for now — mock a guest user so upload/dashboard are accessible
const MOCK_USER = { uid: 'guest', email: 'guest@pitchlens.app' } as User;

export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <AuthContext.Provider value={{ user: MOCK_USER, loading: false }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuthContext = () => useContext(AuthContext);
