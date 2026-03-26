import { useEffect, useState } from 'react';
import { Shield, Trash2, UserCheck, UserX } from 'lucide-react';
import { listUsers, updateUser, deleteUser, UserRecord } from '../services/api';

export default function AdminView() {
    const [users, setUsers] = useState<UserRecord[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionLoading, setActionLoading] = useState<string | null>(null);

    const fetchUsers = async () => {
        setLoading(true);
        setError('');
        try {
            const data = await listUsers();
            setUsers(data);
        } catch (err: any) {
            setError(err.message || 'Failed to load users.');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchUsers(); }, []);

    const handleToggle = async (user: UserRecord, field: 'is_active' | 'is_admin') => {
        const key = `${user.user_id}-${field}`;
        setActionLoading(key);
        try {
            await updateUser(user.user_id, { [field]: !user[field] });
            await fetchUsers();
        } catch (err: any) {
            alert(err.message || 'Update failed.');
        } finally {
            setActionLoading(null);
        }
    };

    const handleDelete = async (user: UserRecord) => {
        if (!confirm(`Delete user "${user.username}" and all their data? This cannot be undone.`)) return;
        setActionLoading(`${user.user_id}-delete`);
        try {
            await deleteUser(user.user_id);
            await fetchUsers();
        } catch (err: any) {
            alert(err.message || 'Delete failed.');
        } finally {
            setActionLoading(null);
        }
    };

    const cell: React.CSSProperties = {
        padding: '12px 16px',
        borderBottom: '1px solid rgba(255,255,255,0.07)',
        color: '#d1d5db',
        fontSize: '0.88rem',
        whiteSpace: 'nowrap',
    };

    const th: React.CSSProperties = {
        ...cell,
        color: '#9ca3af',
        fontWeight: 600,
        fontSize: '0.8rem',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        background: 'rgba(255,255,255,0.03)',
    };

    return (
        <div style={{ padding: '32px', maxWidth: '1000px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '24px' }}>
                <Shield size={22} color="var(--accent-blue, #2563eb)" />
                <h1 style={{ margin: 0, fontSize: '1.4rem', color: '#f3f4f6' }}>User Management</h1>
            </div>

            {error && (
                <div style={{
                    background: 'rgba(239,68,68,0.15)',
                    border: '1px solid rgba(239,68,68,0.4)',
                    borderRadius: '8px',
                    padding: '12px 16px',
                    color: '#fca5a5',
                    marginBottom: '16px',
                }}>
                    {error}
                </div>
            )}

            {loading ? (
                <p style={{ color: '#9ca3af' }}>Loading users…</p>
            ) : (
                <div style={{
                    background: 'var(--sellafield-bg-card, #1a2535)',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: '10px',
                    overflow: 'hidden',
                }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                            <tr>
                                <th style={th}>Username</th>
                                <th style={th}>Joined</th>
                                <th style={th}>Projects</th>
                                <th style={th}>Sessions</th>
                                <th style={{ ...th, textAlign: 'center' }}>Admin</th>
                                <th style={{ ...th, textAlign: 'center' }}>Active</th>
                                <th style={{ ...th, textAlign: 'center' }}>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {users.map(u => (
                                <tr key={u.user_id} style={{ opacity: u.is_active ? 1 : 0.5 }}>
                                    <td style={cell}>
                                        <span style={{ fontWeight: 500, color: '#f3f4f6' }}>{u.username}</span>
                                        {u.is_admin && (
                                            <span style={{
                                                marginLeft: '8px',
                                                fontSize: '0.7rem',
                                                background: 'rgba(37,99,235,0.25)',
                                                color: '#93c5fd',
                                                padding: '2px 6px',
                                                borderRadius: '4px',
                                            }}>
                                                Admin
                                            </span>
                                        )}
                                    </td>
                                    <td style={cell}>
                                        {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
                                    </td>
                                    <td style={{ ...cell, textAlign: 'right' }}>{u.projects_count}</td>
                                    <td style={{ ...cell, textAlign: 'right' }}>{u.sessions_count}</td>

                                    {/* Admin toggle */}
                                    <td style={{ ...cell, textAlign: 'center' }}>
                                        <button
                                            onClick={() => handleToggle(u, 'is_admin')}
                                            disabled={!!actionLoading}
                                            title={u.is_admin ? 'Remove admin' : 'Make admin'}
                                            style={{
                                                background: u.is_admin ? 'rgba(37,99,235,0.3)' : 'rgba(255,255,255,0.07)',
                                                border: 'none',
                                                borderRadius: '6px',
                                                padding: '5px 10px',
                                                cursor: 'pointer',
                                                color: u.is_admin ? '#93c5fd' : '#6b7280',
                                                fontSize: '0.8rem',
                                                fontWeight: 600,
                                            }}
                                        >
                                            {u.is_admin ? 'Yes' : 'No'}
                                        </button>
                                    </td>

                                    {/* Active toggle */}
                                    <td style={{ ...cell, textAlign: 'center' }}>
                                        <button
                                            onClick={() => handleToggle(u, 'is_active')}
                                            disabled={!!actionLoading}
                                            title={u.is_active ? 'Deactivate' : 'Activate'}
                                            style={{
                                                background: u.is_active ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)',
                                                border: 'none',
                                                borderRadius: '6px',
                                                padding: '5px 8px',
                                                cursor: 'pointer',
                                                color: u.is_active ? '#86efac' : '#fca5a5',
                                                display: 'flex',
                                                alignItems: 'center',
                                                gap: '4px',
                                                margin: '0 auto',
                                            }}
                                        >
                                            {u.is_active
                                                ? <><UserCheck size={14} /> Active</>
                                                : <><UserX size={14} /> Inactive</>
                                            }
                                        </button>
                                    </td>

                                    {/* Delete */}
                                    <td style={{ ...cell, textAlign: 'center' }}>
                                        <button
                                            onClick={() => handleDelete(u)}
                                            disabled={!!actionLoading}
                                            title="Delete user and all data"
                                            style={{
                                                background: 'rgba(239,68,68,0.15)',
                                                border: 'none',
                                                borderRadius: '6px',
                                                padding: '5px 8px',
                                                cursor: 'pointer',
                                                color: '#fca5a5',
                                                display: 'flex',
                                                alignItems: 'center',
                                                gap: '4px',
                                                margin: '0 auto',
                                            }}
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}