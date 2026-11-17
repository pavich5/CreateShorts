import { StrictMode, Suspense, lazy } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import { CampaignProvider } from './components/Campaigns';
import { AuthProvider } from './contexts/AuthContext';

const App = lazy(() => import('./App.jsx'));
document.title = 'CreateShorts · Local Studio';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider>
      <Suspense fallback={<div className="min-h-screen bg-paper flex items-center justify-center text-muted">Opening your workspace…</div>}>
        <CampaignProvider><App /></CampaignProvider>
      </Suspense>
    </AuthProvider>
  </StrictMode>,
);
