import { NavLink } from 'react-router-dom';
import { MessageSquare, FileText, Settings, Activity, ListChecks } from 'lucide-react';

const Sidebar = () => {
    return (
        <aside className="sidebar">
            <div className="sidebar-header">
                <div className="logo-icon">
                    <Activity size={20} />
                </div>
                <div className="sidebar-title">NDA Narrative</div>
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
            </nav>

            <div style={{ marginTop: 'auto', padding: '12px', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                <p>Azure Connected</p>
            </div>
        </aside>
    );
};

export default Sidebar;
