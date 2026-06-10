import { useState, useEffect } from 'react';
import InsightFeed from './components/InsightFeed';
import RepoConnector from './components/RepoConnector';
import CodebaseMap from './components/CodebaseMap';
import ElephantMascot from './components/ElephantMascot';
import GlobalProgressBar from './components/GlobalProgressBar';

const NAV_ITEMS = [
  { id: 'insights', label: 'Insights' },
  { id: 'graph', label: 'Graph' },
];

function App() {
  const [activeView, setActiveView] = useState('insights');
  const [repo, setRepo] = useState(null); // { repoId, repoName }
  const [syncProgress, setSyncProgress] = useState({ active: false, progress: 0, label: '', startTime: null });
  const [lastSynced, setLastSynced] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [theme, setTheme] = useState('dark');

  const repoId = repo?.repoId;
  const repoName = repo?.repoName;

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme(prev => prev === 'dark' ? 'light' : 'dark');
  };

  const handleSync = async () => {
    if (!repoId || syncing) return;
    setSyncing(true);
    try {
      const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
      setSyncProgress({ active: true, progress: 5, label: 'Starting sync...', startTime: Date.now() });

      const res = await fetch(`${API_BASE}/api/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_id: repoId }),
      });
      if (!res.ok) throw new Error('Sync request failed');
      
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data:')) {
            const raw = line.slice(5).trim();
            if (!raw) continue;
            try {
              const evt = JSON.parse(raw);
              if (evt.type === 'status') {
                const phase = evt.phase;
                let percent = 50;
                if (phase === 'clone') percent = 15;
                if (phase === 'diff') percent = 30;
                if (phase === 'parse') percent = 45;
                if (phase === 'insert') percent = 60;
                if (phase === 'graph') percent = 75;
                if (phase === 'resolve') percent = 90;
                setSyncProgress(prev => ({ ...prev, progress: percent, label: evt.message }));
              } else if (evt.type === 'complete' || evt.type === 'done') {
                setSyncProgress(prev => ({ ...prev, progress: 100, label: 'Sync complete!' }));
                setTimeout(() => setSyncProgress({ active: false, progress: 0, label: '', startTime: null }), 2000);
                setLastSynced(new Date().toLocaleString());
              } else if (evt.type === 'error') {
                setSyncProgress({ active: false, progress: 0, label: '', startTime: null });
                setSyncing(false);
              }
            } catch {
              /* skip */
            }
          }
        }
      }
    } catch {
      /* sync errors shown in feed */
    } finally {
      setSyncing(false);
    }
  };

  const renderCenterPanel = () => {
    if (!repo) {
      return <RepoConnector onConnected={setRepo} onProgressUpdate={setSyncProgress} />;
    }
    if (activeView === 'graph') {
      return <CodebaseMap repoId={repoId} />;
    }
    return <InsightFeed repoId={repoId} />;
  };

  return (
    <div className="app-shell">
      <GlobalProgressBar {...syncProgress} />
      
      {/* ── Sidebar ────────────────────────────────────────── */}
      <aside className="panel sidebar-panel">
        <div className="sidebar-wordmark">Codebase Memory</div>

        {repoId && (
          <>
            <div className="sidebar-section">
              <div className="sidebar-label">Repository</div>
              <select
                id="repo-selector"
                className="sidebar-select"
                value={repoId}
                readOnly
              >
                <option value={repoId}>{repoName || repoId}</option>
              </select>
              <button 
                id="disconnect-button"
                className="sidebar-disconnect-btn"
                onClick={() => {
                  setRepo(null);
                  setLastSynced(null);
                }}
                style={{
                  marginTop: '8px',
                  width: '100%',
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-muted)',
                  padding: '6px',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                  transition: 'all 0.2s ease'
                }}
                onMouseOver={(e) => {
                  e.target.style.background = 'var(--severity-critical)';
                  e.target.style.color = '#fff';
                  e.target.style.borderColor = 'var(--severity-critical)';
                }}
                onMouseOut={(e) => {
                  e.target.style.background = 'var(--bg-elevated)';
                  e.target.style.color = 'var(--text-muted)';
                  e.target.style.borderColor = 'var(--border)';
                }}
              >
                Disconnect Repository
              </button>
            </div>

            <ul className="sidebar-nav">
              {NAV_ITEMS.map((item) => (
                <li key={item.id}>
                  <button
                    id={`nav-${item.id}`}
                    className="sidebar-nav-item"
                    data-active={activeView === item.id}
                    onClick={() => setActiveView(item.id)}
                  >
                    {item.label}
                  </button>
                </li>
              ))}
            </ul>

            <div className="sidebar-footer">
              <button
                id="sync-button"
                className="sidebar-sync-btn"
                onClick={handleSync}
                disabled={syncing}
              >
                {syncing ? 'Syncing…' : 'Sync'}
              </button>
              {lastSynced && (
                <div className="sidebar-timestamp">Last synced {lastSynced}</div>
              )}
              
              <button
                className="theme-toggle-btn"
                onClick={toggleTheme}
              >
                {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
              </button>
            </div>
          </>
        )}
        
        {!repoId && (
          <div className="sidebar-footer" style={{ marginTop: 'auto' }}>
            <button
              className="theme-toggle-btn"
              onClick={toggleTheme}
            >
              {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
            </button>
          </div>
        )}
      </aside>

      {/* ── Center panel ───────────────────────────────────── */}
      <main className="panel center-panel">
        {renderCenterPanel()}
      </main>

      {/* ── Floating Mascot ────────────────────────────────── */}
      <ElephantMascot repoId={repoId} />
    </div>
  );
}

export default App;
