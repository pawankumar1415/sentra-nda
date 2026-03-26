import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { loginUser, registerUser } from '../services/api';

export default function LoginView() {
    const { login } = useAuth();
    const navigate = useNavigate();

    const [mode, setMode] = useState<'login' | 'register'>('login');
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        setError('');
        setLoading(true);

        try {
            const result = mode === 'login'
                ? await loginUser(username, password)
                : await registerUser(username, password);

            login(result.token, result.username, result.is_admin);
            navigate('/chat', { replace: true });
        } catch (err: any) {
            setError(err.message || 'An error occurred. Please try again.');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: '100vh',
            width: '100%',
            background: 'var(--sellafield-bg-dark, #0f1923)',
        }}>
            <div style={{
                background: 'var(--sellafield-bg-card, #1a2535)',
                border: '1px solid var(--sellafield-dark-teal, #2a3f5f)',
                borderRadius: '12px',
                padding: '40px',
                width: '100%',
                maxWidth: '400px',
            }}>
                {/* Logo */}
                <div style={{ textAlign: 'center', marginBottom: '32px' }}>
                    <img
                        src="https://sellafield-sentra.netlify.app/assets/sellafield-logo.png"
                        alt="Sellafield"
                        style={{ width: '140px', objectFit: 'contain' }}
                    />
                    <p style={{ color: '#9ca3af', fontSize: '0.85rem', marginTop: '8px' }}>
                        NDA Narrative Validation System
                    </p>
                </div>

                {/* Tab switcher */}
                <div style={{
                    display: 'flex',
                    background: 'rgba(255,255,255,0.05)',
                    borderRadius: '8px',
                    padding: '4px',
                    marginBottom: '24px',
                }}>
                    {(['login', 'register'] as const).map(tab => (
                        <button
                            key={tab}
                            onClick={() => { setMode(tab); setError(''); }}
                            style={{
                                flex: 1,
                                padding: '8px',
                                border: 'none',
                                borderRadius: '6px',
                                cursor: 'pointer',
                                fontSize: '0.9rem',
                                fontWeight: mode === tab ? 600 : 400,
                                background: mode === tab ? 'var(--accent-blue, #2563eb)' : 'transparent',
                                color: mode === tab ? '#fff' : '#9ca3af',
                                transition: 'all 0.2s',
                            }}
                        >
                            {tab === 'login' ? 'Sign In' : 'Register'}
                        </button>
                    ))}
                </div>

                {/* Form */}
                <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                    <div>
                        <label style={{ display: 'block', color: '#d1d5db', fontSize: '0.85rem', marginBottom: '6px' }}>
                            Username
                        </label>
                        <input
                            type="text"
                            value={username}
                            onChange={e => setUsername(e.target.value)}
                            required
                            autoComplete="username"
                            style={{
                                width: '100%',
                                padding: '10px 12px',
                                background: 'rgba(255,255,255,0.07)',
                                border: '1px solid rgba(255,255,255,0.15)',
                                borderRadius: '8px',
                                color: '#f3f4f6',
                                fontSize: '0.95rem',
                                outline: 'none',
                                boxSizing: 'border-box',
                            }}
                        />
                    </div>

                    <div>
                        <label style={{ display: 'block', color: '#d1d5db', fontSize: '0.85rem', marginBottom: '6px' }}>
                            Password
                        </label>
                        <input
                            type="password"
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                            required
                            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                            style={{
                                width: '100%',
                                padding: '10px 12px',
                                background: 'rgba(255,255,255,0.07)',
                                border: '1px solid rgba(255,255,255,0.15)',
                                borderRadius: '8px',
                                color: '#f3f4f6',
                                fontSize: '0.95rem',
                                outline: 'none',
                                boxSizing: 'border-box',
                            }}
                        />
                    </div>

                    {error && (
                        <div style={{
                            background: 'rgba(239,68,68,0.15)',
                            border: '1px solid rgba(239,68,68,0.4)',
                            borderRadius: '8px',
                            padding: '10px 12px',
                            color: '#fca5a5',
                            fontSize: '0.85rem',
                        }}>
                            {error}
                        </div>
                    )}

                    <button
                        type="submit"
                        disabled={loading}
                        style={{
                            padding: '11px',
                            background: loading ? '#374151' : 'var(--accent-blue, #2563eb)',
                            color: '#fff',
                            border: 'none',
                            borderRadius: '8px',
                            fontSize: '0.95rem',
                            fontWeight: 600,
                            cursor: loading ? 'not-allowed' : 'pointer',
                            transition: 'background 0.2s',
                        }}
                    >
                        {loading ? 'Please wait…' : mode === 'login' ? 'Sign In' : 'Create Account'}
                    </button>
                </form>

                {mode === 'register' && (
                    <p style={{ color: '#6b7280', fontSize: '0.8rem', textAlign: 'center', marginTop: '16px' }}>
                        The first registered account is automatically granted admin access.
                    </p>
                )}
            </div>
        </div>
    );
}