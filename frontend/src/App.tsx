import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import ChatView from './views/ChatView';
import ValidateView from './views/ValidateView';
import IngestView from './views/IngestView';
import './App.css';

function App() {
  return (
    <Router>
      <div className="app-container">
        <Sidebar />
        <main className="main-content">
          <Routes>
            <Route path="/" element={<Navigate to="/chat" replace />} />
            <Route path="/chat" element={<ChatView />} />
            <Route path="/validate" element={<ValidateView />} />
            <Route path="/ingest" element={<IngestView />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;
