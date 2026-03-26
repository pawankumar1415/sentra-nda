import { NavLink, useNavigate } from 'react-router-dom';
import { MessageSquare, FileText, Settings, ListChecks, Shield, LogOut } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const Sidebar = () => {
    const { user, logout } = useAuth();
    const navigate = useNavigate();

    const handleLogout = () => {
        logout();
        navigate('/login', { replace: true });
    };

    return (
        <aside className="sidebar">
            <div className="sidebar-header" style={{ display: 'flex', justifyContent: 'center', marginBottom: '40px' }}>
                <img src="https://sellafield-sentra.netlify.app/assets/sellafield-logo.png" alt="Sellafield Logo" style={{ width: '160px', objectFit: 'contain' }} />
            </div>

            <nav className="nav-links">
                <NavLink
                    to="/chat"
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                    <MessageSquare size={18} />
                    <span>QA Chat</span>
                </NavLink>

                <NavLink
                    to="/validate"
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                    <FileText size={18} />
                    <span>Individual Narrative Validation</span>
                </NavLink>

                <NavLink
                    to="/batch-validate"
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                    <ListChecks size={18} />
                    <span>Batch Narrative Validation</span>
                </NavLink>

                <NavLink
                    to="/ingest"
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                    <Settings size={18} />
                    <span>Data Upload</span>
                </NavLink>

                {user?.is_admin && (
                    <NavLink
                        to="/admin"
                        className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                    >
                        <Shield size={18} />
                        <span>Admin</span>
                    </NavLink>
                )}
            </nav>

            {/* User info + logout */}
            <div style={{
                marginTop: 'auto',
                padding: '12px 8px',
                borderTop: '1px solid rgba(255,255,255,0.08)',
            }}>
                {user && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
                        <div style={{
                            width: '28px',
                            height: '28px',
                            borderRadius: '50%',
                            background: 'var(--accent-blue, #2563eb)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            color: '#fff',
                            fontSize: '0.75rem',
                            fontWeight: 700,
                            flexShrink: 0,
                        }}>
                            {user.username[0].toUpperCase()}
                        </div>
                        <div style={{ overflow: 'hidden', flex: 1 }}>
                            <p style={{ margin: 0, fontSize: '0.82rem', color: '#e5e7eb', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {user.username}
                            </p>
                            {user.is_admin && (
                                <p style={{ margin: 0, fontSize: '0.7rem', color: '#93c5fd' }}>Admin</p>
                            )}
                        </div>
                    </div>
                )}
                <button
                    onClick={handleLogout}
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        width: '100%',
                        padding: '7px 10px',
                        background: 'transparent',
                        border: '1px solid rgba(255,255,255,0.1)',
                        borderRadius: '6px',
                        color: '#9ca3af',
                        fontSize: '0.82rem',
                        cursor: 'pointer',
                        transition: 'background 0.2s',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'rgba(239,68,68,0.1)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                    <LogOut size={14} />
                    Sign Out
                </button>
            </div>
        </aside>
    );
};

export default Sidebar;
