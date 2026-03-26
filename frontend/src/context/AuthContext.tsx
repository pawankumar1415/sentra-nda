import { createContext, useContext, useState, useCallback } from 'react';
import type { ReactNode } from 'react';

interface AuthUser {
    token: string;
    username: string;
    is_admin: boolean;
}

interface AuthContextValue {
    user: AuthUser | null;
    login: (token: string, username: string, is_admin: boolean) => void;
    logout: () => void;
    isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const STORAGE_KEY = 'nda_auth';

function loadStoredUser(): AuthUser | null {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<AuthUser | null>(loadStoredUser);

    const login = useCallback((token: string, username: string, is_admin: boolean) => {
        const authUser = { token, username, is_admin };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(authUser));
        setUser(authUser);
    }, []);

    const logout = useCallback(() => {
        localStorage.removeItem(STORAGE_KEY);
        setUser(null);
    }, []);

    return (
        <AuthContext.Provider value={{ user, login, logout, isAuthenticated: !!user }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth(): AuthContextValue {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
    return ctx;
}