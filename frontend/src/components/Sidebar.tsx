import { NavLink } from 'react-router-dom';
import { MessageSquare, FileText, Settings, ListChecks } from 'lucide-react';

const Sidebar = () => {
    return (
        <aside className="sidebar">
            <div className="sidebar-header" style={{ display: 'flex', justifyContent: 'center', marginBottom: '40px', padding: '16px', background: 'var(--sellafield-dark-teal)', borderRadius: '8px' }}>
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
            </nav>

            <div style={{ marginTop: 'auto', padding: '12px', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                <p>Azure Connected</p>
            </div>
        </aside>
    );
};

export default Sidebar;
