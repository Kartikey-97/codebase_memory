import { useState, useRef, useCallback } from 'react';
import styles from './styles.module.css';
import { IS_MOCK, runMockIngest } from '../../mock';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

const STEPS = [
  { id: 'clone', label: 'Cloning repository' },
  { id: 'parse', label: 'Parsing files' },
  { id: 'insert', label: 'Generating embeddings' },
  { id: 'graph', label: 'Building relationship graph' },
  { id: 'insights', label: 'Generating insights' },
];

// Map SSE phase → step id
const PHASE_TO_STEP = {
  start: null,
  clone: 'clone',
  parse: 'parse',
  insert: 'insert',
  graph: 'graph',
  insights: 'insights',
};

function RepoConnector({ onConnected, onProgressUpdate }) {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [stepStates, setStepStates] = useState({}); // { clone: 'done', parse: 'active', ... }
  const [progress, setProgress] = useState(0); // 0–100
  const [fadeOut, setFadeOut] = useState(false);
  const abortRef = useRef(null);

  const advanceToStep = useCallback(
    (stepId) => {
      if (!stepId) return;
      setStepStates((prev) => {
        const next = { ...prev };
        // Mark all prior steps as done.
        for (const step of STEPS) {
          if (step.id === stepId) break;
          if (next[step.id] !== 'done') next[step.id] = 'done';
        }
        next[stepId] = 'active';
        return next;
      });
      // Compute progress %.
      const idx = STEPS.findIndex((s) => s.id === stepId);
      if (idx >= 0) {
        const pct = idx === STEPS.length - 1 ? 90 : Math.round(((idx + 1) / STEPS.length) * 100);
        setProgress(pct);
        if (onProgressUpdate) {
          onProgressUpdate(prev => ({
            ...prev,
            progress: pct,
            label: STEPS[idx].label
          }));
        }
      }
    },
    [onProgressUpdate]
  );

  const handleConnect = async () => {
    const trimmed = url.trim();
    if (!trimmed || loading) return;

    setLoading(true);
    setError(null);
    setStepStates({ clone: 'active' });
    setProgress(5);
    setFadeOut(false);

    if (onProgressUpdate) {
      onProgressUpdate({
        active: true,
        progress: 5,
        label: 'Cloning repository...',
        startTime: Date.now()
      });
    }

    // ── Mock mode: fake SSE events ──
    if (IS_MOCK) {
      const cancel = runMockIngest((evt) => handleSSEEvent(evt));
      abortRef.current = { abort: cancel };
      return;
    }

    // ── Real mode: POST /api/ingest ──
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(`${API_BASE}/api/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: trimmed }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
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
              handleSSEEvent(evt);
            } catch {
              /* skip malformed JSON */
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        setError(err.message || 'Connection failed.');
        setLoading(false);
      }
    }
  };

  const handleSSEEvent = (evt) => {
    const type = evt.type || evt.event;

    if (type === 'status') {
      const phase = evt.phase;
      const stepId = PHASE_TO_STEP[phase];
      if (stepId) advanceToStep(stepId);
      return;
    }

    if (type === 'complete') {
      // Mark all steps done.
      setStepStates(() => {
        const final = {};
        for (const step of STEPS) final[step.id] = 'done';
        return final;
      });
      setProgress(100);
      if (onProgressUpdate) {
        onProgressUpdate(prev => ({ ...prev, progress: 100, label: 'Ingestion complete!' }));
        setTimeout(() => onProgressUpdate({ active: false, progress: 0, label: '', startTime: null }), 2000);
      }

      // Fade out then transition.
      setTimeout(() => {
        setFadeOut(true);
        setTimeout(() => {
          onConnected({
            repoId: evt.repo_id,
            repoName: evt.repo_name || extractRepoName(url),
          });
        }, 300);
      }, 400);
      return;
    }

    if (type === 'error') {
      setError(evt.message || 'Ingestion failed.');
      setLoading(false);
      setStepStates({}); //i added this to fix the frozen ui
      if (onProgressUpdate) {
        onProgressUpdate({ active: false, progress: 0, label: '', startTime: null });
      }
      return;
    }

    if (type === 'done') {
      // Final SSE event — no-op if already handled.
      return;
    }
  };

  const extractRepoName = (repoUrl) => {
    try {
      const parts = repoUrl.replace(/\.git$/, '').split('/');
      return parts[parts.length - 1] || repoUrl;
    } catch {
      return repoUrl;
    }
  };

  const showProgress = loading || Object.keys(stepStates).length > 0;

  return (
    <div className={`${styles.container} ${fadeOut ? styles.fadeOut : ''}`}>
      {showProgress && (
        <div className={styles.progressBarTrack}>
          <div
            className={styles.progressBarFill}
            style={{ width: `${progress}%` }}
          />
        </div>
      )}

      <div className={styles.content}>
        {!showProgress ? (
          <div className={styles.form}>
            <label htmlFor="repo-url-input" className={styles.formLabel}>
              Repository URL
            </label>
            <div className={styles.inputRow}>
              <input
                id="repo-url-input"
                className={styles.input}
                type="url"
                placeholder="https://github.com/owner/repo"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleConnect()}
                disabled={loading}
                autoFocus
              />
              <button
                id="connect-button"
                className={styles.connectBtn}
                onClick={handleConnect}
                disabled={loading || !url.trim()}
              >
                Connect
              </button>
            </div>
          </div>
        ) : (
          <div className={styles.stepLog}>
            {STEPS.map((step) => {
              const state = stepStates[step.id] || 'pending';
              return (
                <div key={step.id} className={styles.stepRow}>
                  <span
                    className={styles.stepDot}
                    data-state={state}
                  />
                  <span
                    className={styles.stepLabel}
                    data-state={state}
                  >
                    {step.label}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {error && (
          <div className={styles.error}>{error}</div>
        )}
      </div>
    </div>
  );
}

export default RepoConnector;
