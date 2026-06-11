import { useState, useEffect, useRef } from 'react';
import styles from './styles.module.css';
import { IS_MOCK, getMockInsights } from '../../mock';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
const POLL_INTERVAL = 5_000;

const SEVERITY_OPTIONS = ['critical', 'warning', 'info', 'suggestion'];
const TYPE_OPTIONS = [
  'repo_overview',
  'stale_docs',
  'dependency_risk',
  'duplicate_logic',
  'ownership_gap',
  'complexity_spike',
  'breaking_change_risk',
  'architecture_suggestion',
  'feature_recommendation',
];

const TYPE_LABELS = {
  repo_overview: 'Repo Overview',
  stale_docs: 'Stale Docs',
  dependency_risk: 'Dependency Risk',
  duplicate_logic: 'Duplicate Logic',
  ownership_gap: 'Ownership Gap',
  complexity_spike: 'Complexity',
  breaking_change_risk: 'Breaking Change',
  architecture_suggestion: 'Architecture',
  feature_recommendation: 'Features',
};

function InsightFeed({ repoId }) {
  const [insights, setInsights] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeSeverities, setActiveSeverities] = useState(new Set(SEVERITY_OPTIONS));
  const [activeType, setActiveType] = useState(null); // null = all
  const [expandedId, setExpandedId] = useState(null);
  const pollRef = useRef(null);
  const mountGenRef = useRef(0); // track fetch generation for animation reset

  const fetchInsights = async (isInitial = false) => {
    if (!repoId) return;

    // Mock mode: load sample data directly.
    if (IS_MOCK) {
      const data = getMockInsights(repoId);
      setInsights(data.insights || []);
      if (isInitial) {
        mountGenRef.current += 1;
        setLoading(false);
      }
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/api/insights?repo_id=${encodeURIComponent(repoId)}`);
      if (!res.ok) return;
      const data = await res.json();
      const items = data.insights || [];
      setInsights(items);
      if (isInitial) mountGenRef.current += 1;
    } catch {
      /* silent poll failure */
    } finally {
      if (isInitial) setLoading(false);
    }
  };

  useEffect(() => {
    setInsights([]);
    setLoading(true);
    setExpandedId(null);
    mountGenRef.current += 1;
    fetchInsights(true);

    pollRef.current = setInterval(() => fetchInsights(false), POLL_INTERVAL);
    return () => clearInterval(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId]);

  // ── Client-side filtering ─────────────────────────────────────

  const toggleSeverity = (sev) => {
    setActiveSeverities((prev) => {
      const next = new Set(prev);
      if (next.has(sev)) {
        if (next.size > 1) next.delete(sev); // keep at least 1
      } else {
        next.add(sev);
      }
      return next;
    });
  };

  const toggleType = (type) => {
    setActiveType((prev) => (prev === type ? null : type));
  };

  const filtered = insights.filter((ins) => {
    if (!activeSeverities.has(ins.severity)) return false;
    if (activeType && ins.type !== activeType) return false;
    return true;
  });

  // ── Render ────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className={styles.container}>
        <div className={styles.empty}>Loading insights…</div>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      {/* Filter bar */}
      <div className={styles.filterBar}>
        <div className={styles.severityToggles}>
          {SEVERITY_OPTIONS.map((sev) => (
            <button
              key={sev}
              id={`filter-severity-${sev}`}
              className={styles.severityBtn}
              data-severity={sev}
              data-active={activeSeverities.has(sev)}
              onClick={() => toggleSeverity(sev)}
            >
              <span className={styles.severityDot} data-severity={sev} />
              {sev.charAt(0).toUpperCase() + sev.slice(1)}
            </button>
          ))}
        </div>
        <div className={styles.typeChips}>
          {TYPE_OPTIONS.map((type) => (
            <button
              key={type}
              id={`filter-type-${type}`}
              className={styles.typeChip}
              data-active={activeType === type}
              onClick={() => toggleType(type)}
            >
              {TYPE_LABELS[type]}
            </button>
          ))}
        </div>
      </div>

      {/* Insight cards or empty state */}
      {filtered.length === 0 ? (
        <div className={styles.empty}>No issues detected in this snapshot.</div>
      ) : (
        <div className={styles.cardList}>
          {filtered.map((ins, idx) => (
            <InsightCard
              key={ins._id || idx}
              insight={ins}
              index={idx}
              expanded={expandedId === (ins._id || idx)}
              onToggle={() =>
                setExpandedId((prev) =>
                  prev === (ins._id || idx) ? null : (ins._id || idx)
                )
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── InsightCard ───────────────────────────────────────────────────

function InsightCard({ insight, index, expanded, onToggle }) {
  const files = insight.affected_files || [];
  const previewFiles = files.slice(0, 3);
  const hasMore = files.length > 3;

  return (
    <div
      className={styles.card}
      data-severity={insight.severity}
      style={{ animationDelay: `${index * 50}ms` }}
      onClick={onToggle}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onToggle()}
    >
      <div className={styles.cardHeader}>
        <span className={styles.cardTitle}>{insight.title}</span>
        <span className={styles.cardTime}>{relativeTime(insight.created_at)}</span>
      </div>

      <div className={styles.cardDesc} style={{ whiteSpace: 'pre-wrap' }}>
        {expanded || insight.type === 'repo_overview' ? insight.description : truncate(insight.description, 120)}
      </div>

      <div className={styles.cardFiles}>
        {(expanded ? files : previewFiles).map((file, i) => (
          <span key={i} className={styles.filePath}>
            {file}
          </span>
        ))}
        {!expanded && hasMore && (
          <span className={styles.fileMore}>+{files.length - 3} more</span>
        )}
      </div>
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────

function truncate(str, max) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '…' : str;
}

function relativeTime(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export default InsightFeed;
