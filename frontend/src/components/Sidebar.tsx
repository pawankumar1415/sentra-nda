import React from 'react';
import { NavLink } from 'react-router-dom';
import { MessageSquare, FileText, Settings, Activity } from 'lucide-react';

const Sidebar = () => {
    return (
        <aside className="sidebar">
            <div className="sidebar-header">
                <div className="logo-icon">
                    <Activity size={20} />
                </div>
                <div className="sidebar-title">Sentra RAG</div>
            </div>

            <nav className="nav-links">
                <NavLink
                    to="/chat"
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                    <MessageSquare size={18} />
                    <span>New Chat</span>
                </NavLink>

                <NavLink
                    to="/validate"
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                    <FileText size={18} />
                    <span>Validate Narrative</span>
                </NavLink>

                <NavLink
                    to="/ingest"
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                    <Settings size={18} />
                    <span>Data Settings</span>
                </NavLink>
            </nav>

            <div style={{ marginTop: 'auto', padding: '12px', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                <p>Azure Connected</p>
            </div>
        </aside>
    );
};

export default Sidebar;
