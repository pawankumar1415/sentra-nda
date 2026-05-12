import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ValidationProvider } from './context/ValidationContext';
import ProtectedRoute from './components/ProtectedRoute';
import Sidebar from './components/Sidebar';
import ChatView from './views/ChatView';
import ValidateView from './views/ValidateView';
import BatchValidateView from './views/BatchValidateView';
import IngestView from './views/IngestView';
import LoginView from './views/LoginView';
import AdminView from './views/AdminView';
import AnalyticsView from './views/AnalyticsView';
import './App.css';

function App() {
  return (
    <AuthProvider>
      <ValidationProvider>
        <Router>
          <Routes>
            {/* Public route — no sidebar */}
            <Route path="/login" element={<LoginView />} />

            {/* Protected routes — with sidebar */}
            <Route
              path="/*"
              element={
                <ProtectedRoute>
                  <div className="app-container">
                    <Sidebar />
                    <main className="main-content">
                      <Routes>
                        <Route path="/" element={<Navigate to="/chat" replace />} />
                        <Route path="/chat" element={<ChatView />} />
                        <Route path="/validate" element={<ValidateView />} />
                        <Route path="/batch-validate" element={<BatchValidateView />} />
                        <Route path="/ingest" element={<IngestView />} />
                        <Route path="/analytics" element={<AnalyticsView />} />
                        <Route
                          path="/admin"
                          element={
                            <ProtectedRoute requireAdmin>
                              <AdminView />
                            </ProtectedRoute>
                          }
                        />
                      </Routes>
                    </main>
                  </div>
                </ProtectedRoute>
              }
            />
          </Routes>
        </Router>
      </ValidationProvider>
    </AuthProvider>
  );
}

export default App;
