import { useState, useEffect, useRef, useCallback } from 'react';
import * as d3 from 'd3';
import styles from './styles.module.css';
import { IS_MOCK, getMockGraphData, getMockInsights } from '../../mock';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

// 5 distinct muted colors for owner hashing.
const OWNER_PALETTE = [
  '#c9a55a', // amber-gold
  '#5a9ec9', // steel-blue
  '#8b6fb0', // muted-purple
  '#5ab88f', // sea-green
  '#c97a5a', // terracotta
];
const UNOWNED_COLOR = '#555';

const EDGE_COLORS = {
  imports: 'rgba(120, 120, 120, 0.35)',
  calls: 'rgba(201, 165, 90, 0.50)',
  extends: 'rgba(90, 158, 201, 0.55)',
};

function ownerColor(owner) {
  if (!owner) return UNOWNED_COLOR;
  let hash = 0;
  for (let i = 0; i < owner.length; i++) {
    hash = owner.charCodeAt(i) + ((hash << 5) - hash);
  }
  return OWNER_PALETTE[Math.abs(hash) % OWNER_PALETTE.length];
}

function basename(path) {
  const parts = path.split('/');
  return parts[parts.length - 1];
}

function CodebaseMap({ repoId }) {
  const svgRef = useRef(null);
  const containerRef = useRef(null);
  const simulationRef = useRef(null);
  const [popover, setPopover] = useState(null);
  const [summary, setSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const [summarizingCluster, setSummarizingCluster] = useState(false);
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const dataRef = useRef(null);

  // ── Fetch data ──────────────────────────────────────────────
  const fetchData = useCallback(async () => {
    if (!repoId) return null;

    if (IS_MOCK) {
      const graph = getMockGraphData(repoId);
      const insightsData = getMockInsights(repoId);
      return { ...graph, insights: insightsData.insights || [] };
    }

    try {
      const [relRes, fileRes, insRes] = await Promise.all([
        fetch(`${API_BASE}/api/relationships?repo_id=${encodeURIComponent(repoId)}`),
        fetch(`${API_BASE}/api/files?repo_id=${encodeURIComponent(repoId)}`),
        fetch(`${API_BASE}/api/insights?repo_id=${encodeURIComponent(repoId)}`),
      ]);
      const relationships = relRes.ok ? (await relRes.json()).relationships || [] : [];
      const files = fileRes.ok ? (await fileRes.json()).files || [] : [];
      const insights = insRes.ok ? (await insRes.json()).insights || [] : [];
      return { files, relationships, insights };
    } catch {
      return null;
    }
  }, [repoId]);

  // ── Fetch data on repo change ─────────────────────────────────
  useEffect(() => {
    if (!repoId) return;
    setLoading(true);
    setPopover(null);

    fetchData().then((data) => {
      dataRef.current = data;
      setLoading(false);
    });

    return () => {
      if (simulationRef.current) simulationRef.current.stop();
    };
  }, [repoId, fetchData]);

  // ── Build graph AFTER container is rendered ───────────────────
  useEffect(() => {
    if (loading || !dataRef.current) return;
    // Wait one frame so the container has layout dimensions.
    const raf = requestAnimationFrame(() => {
      buildGraph(dataRef.current, filterText);
    });
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, filterText]);

  const buildGraph = useCallback((data, currentFilter) => {
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const container = containerRef.current;
    if (!container) return;
    const width = container.clientWidth;
    const height = container.clientHeight;

    svg.attr('width', width).attr('height', height);

    const { files, relationships, insights } = data;

    // Build node + link data.
    const dependentCount = {};
    relationships.forEach((r) => {
      dependentCount[r.to_file] = (dependentCount[r.to_file] || 0) + 1;
    });

    const insightsByFile = {};
    insights.forEach((ins) => {
      (ins.affected_files || []).forEach((f) => {
        if (!insightsByFile[f]) insightsByFile[f] = [];
        insightsByFile[f].push(ins.title);
      });
    });

    const nodeMap = {};
    const nodes = files
      .filter((f) => !currentFilter || f.path.toLowerCase().includes(currentFilter.toLowerCase()))
      .map((f) => {
        const deps = dependentCount[f.path] || 0;
        const node = {
          id: f.path,
          owner: f.owner || '',
          doc_coverage: f.doc_coverage || 0,
          dependents: deps,
          radius: Math.max(4, Math.min(16, 4 + deps * 1.5)),
          color: (() => {
            const pathStr = f.path.toLowerCase();
            if (pathStr.includes('src/') || pathStr.includes('components/') || pathStr.includes('pages/') || pathStr.includes('frontend/') || pathStr.endsWith('.jsx') || pathStr.endsWith('.tsx') || pathStr.endsWith('.ts')) return 'var(--accent-emerald)';
            if (pathStr.includes('app/') || pathStr.includes('api/') || pathStr.includes('backend/') || pathStr.endsWith('.py') || pathStr.endsWith('.go')) return 'var(--accent-amber)';
            if (pathStr.endsWith('.json') || pathStr.endsWith('.yaml') || pathStr.endsWith('.md')) return 'var(--accent-slate)';
            return 'var(--text-secondary)';
          })(),
          insights: insightsByFile[f.path] || [],
        };
        nodeMap[f.path] = node;
        return node;
      });

    const links = relationships
      .filter((r) => nodeMap[r.from_file] && nodeMap[r.to_file])
      .map((r) => ({
        source: r.from_file,
        target: r.to_file,
        type: r.type,
        weight: r.weight,
      }));

    // D3 force simulation.
    const simulation = d3
      .forceSimulation(nodes)
      .force(
        'link',
        d3
          .forceLink(links)
          .id((d) => d.id)
          .distance(80)
      )
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius((d) => d.radius + 4));

    simulationRef.current = simulation;

    // Container group for zoom/pan.
    const g = svg.append('g');

    svg.call(
      d3
        .zoom()
        .scaleExtent([0.3, 4])
        .on('zoom', (event) => g.attr('transform', event.transform))
    );

    // Edges.
    const link = g
      .append('g')
      .selectAll('line')
      .data(links)
      .join('line')
      .attr('stroke', (d) => EDGE_COLORS[d.type] || EDGE_COLORS.imports)
      .attr('stroke-width', (d) => Math.min(3, 0.5 + d.weight * 0.3));

    // Nodes.
    const node = g
      .append('g')
      .selectAll('circle')
      .data(nodes)
      .join('circle')
      .attr('r', (d) => d.radius)
      .attr('fill', (d) => d.color)
      .attr('stroke', 'rgba(255,255,255,0.08)')
      .attr('stroke-width', 1)
      .attr('cursor', 'pointer')
      .call(drag(simulation));

    // Labels for larger nodes.
    const label = g
      .append('g')
      .selectAll('text')
      .data(nodes.filter((n) => n.dependents >= 2))
      .join('text')
      .text((d) => basename(d.id))
      .attr('font-size', 10)
      .attr('font-family', 'var(--font-mono), monospace')
      .attr('fill', 'var(--text-secondary)')
      .attr('pointer-events', 'none')
      .attr('dx', (d) => d.radius + 4)
      .attr('dy', 3);

    // Hover highlight.
    const neighbors = new Set();

    node
      .on('mouseenter', (_event, d) => {
        neighbors.clear();
        neighbors.add(d.id);
        links.forEach((l) => {
          const src = typeof l.source === 'object' ? l.source.id : l.source;
          const tgt = typeof l.target === 'object' ? l.target.id : l.target;
          if (src === d.id) neighbors.add(tgt);
          if (tgt === d.id) neighbors.add(src);
        });

        node.attr('opacity', (n) => (neighbors.has(n.id) ? 1 : 0.15));
        link.attr('opacity', (l) => {
          const src = typeof l.source === 'object' ? l.source.id : l.source;
          const tgt = typeof l.target === 'object' ? l.target.id : l.target;
          return neighbors.has(src) && neighbors.has(tgt) ? 1 : 0.08;
        });
        label.attr('opacity', (n) => (neighbors.has(n.id) ? 1 : 0.15));
      })
      .on('mouseleave', () => {
        node.attr('opacity', 1);
        link.attr('opacity', 1);
        label.attr('opacity', 1);
      })
      .on('click', (event, d) => {
        event.stopPropagation();
        const rect = containerRef.current.getBoundingClientRect();
        setPopover({
          path: d.id,
          owner: d.owner || 'Unowned',
          docCoverage: d.doc_coverage,
          dependents: d.dependents,
          insights: d.insights,
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
        });
        setSummary(null);
        setSummarizing(false);
        setSummarizingCluster(false);
      });

    svg.on('click', () => setPopover(null));

    // Tick.
    simulation.on('tick', () => {
      link
        .attr('x1', (d) => d.source.x)
        .attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x)
        .attr('y2', (d) => d.target.y);

      node.attr('cx', (d) => d.x).attr('cy', (d) => d.y);

      label.attr('x', (d) => d.x).attr('y', (d) => d.y);
    });
  }, [repoId]);

  const handleSummarize = async () => {
    if (!popover || !repoId) return;
    setSummarizing(true);
    try {
      const res = await fetch(`${API_BASE}/api/graph/summarize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_id: repoId, path: popover.path }),
      });
      if (res.ok) {
        const data = await res.json();
        setSummary(data.summary);
      } else {
        setSummary('Failed to summarize.');
      }
    } catch {
      setSummary('Failed to summarize.');
    } finally {
      setSummarizing(false);
    }
  };

  const handleSummarizeCluster = async () => {
    if (!popover || !repoId) return;
    setSummarizingCluster(true);
    try {
      const res = await fetch(`${API_BASE}/api/graph/summarize_cluster`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_id: repoId, path: popover.path }),
      });
      if (res.ok) {
        const data = await res.json();
        setSummary(data.summary);
      } else {
        setSummary('Failed to summarize cluster.');
      }
    } catch {
      setSummary('Failed to summarize cluster.');
    } finally {
      setSummarizingCluster(false);
    }
  };

  // ── No-repo placeholder ─────────────────────────────────────
  if (!repoId) {
    return (
      <div className={styles.container}>
        <div className={styles.empty}>Connect a repository to view the codebase graph.</div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className={styles.container}>
        <div className={styles.empty}>Loading graph…</div>
      </div>
    );
  }

  return (
    <div className={styles.container} ref={containerRef}>
      {/* Legend */}
      <div className={styles.legend}>
        <div className={styles.legendSection}>
          <span className={styles.legendTitle}>Nodes</span>
          <span className={styles.legendItem}>
            <span className={styles.legendLine} style={{ background: 'var(--accent-emerald)', width: '8px', height: '8px', borderRadius: '50%' }} />
            frontend
          </span>
          <span className={styles.legendItem}>
            <span className={styles.legendLine} style={{ background: 'var(--accent-amber)', width: '8px', height: '8px', borderRadius: '50%' }} />
            backend
          </span>
          <span className={styles.legendItem}>
            <span className={styles.legendLine} style={{ background: 'var(--accent-slate)', width: '8px', height: '8px', borderRadius: '50%' }} />
            config
          </span>
        </div>
        <div className={styles.legendSection}>
          <span className={styles.legendTitle}>Edges</span>
          <span className={styles.legendItem}>
            <span className={styles.legendLine} style={{ background: 'rgba(120,120,120,0.6)' }} />
            imports
          </span>
          <span className={styles.legendItem}>
            <span className={styles.legendLine} style={{ background: 'rgba(201,165,90,0.8)' }} />
            calls
          </span>
          <span className={styles.legendItem}>
            <span className={styles.legendLine} style={{ background: 'rgba(90,158,201,0.8)' }} />
            extends
          </span>
        </div>
        <div className={styles.legendSection}>
          <span className={styles.legendTitle}>Node size</span>
          <span className={styles.legendItem}>= dependents</span>
        </div>
      </div>

      {/* Filter */}
      <div className={styles.filterContainer}>
        <input
          type="text"
          className={styles.filterInput}
          placeholder="Filter files by path..."
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
        />
        <div className={styles.filterPills} style={{ display: 'flex', gap: '6px', marginTop: '8px' }}>
          <button className={styles.filterPill} onClick={() => setFilterText('frontend')} style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: '4px 8px', borderRadius: '4px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}>Frontend</button>
          <button className={styles.filterPill} onClick={() => setFilterText('backend')} style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: '4px 8px', borderRadius: '4px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}>Backend</button>
          <button className={styles.filterPill} onClick={() => setFilterText('.json')} style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: '4px 8px', borderRadius: '4px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}>Config</button>
          <button className={styles.filterPill} onClick={() => setFilterText('')} style={{ background: 'transparent', border: 'none', padding: '4px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-muted)' }}>Clear</button>
        </div>
      </div>

      <svg ref={svgRef} className={styles.svg} />

      {/* Popover */}
      {popover && (
        <div
          className={styles.popover}
          style={{
            left: Math.min(popover.x, (containerRef.current?.clientWidth || 600) - 260),
            top: Math.min(popover.y, (containerRef.current?.clientHeight || 400) - 180),
          }}
        >
          <div className={styles.popoverPath}>{popover.path}</div>
          <div className={styles.popoverRow}>
            <span className={styles.popoverLabel}>Owner</span>
            <span>{popover.owner}</span>
          </div>
          <div className={styles.popoverRow}>
            <span className={styles.popoverLabel}>Doc coverage</span>
            <span>{Math.round(popover.docCoverage * 100)}%</span>
          </div>
          <div className={styles.popoverRow}>
            <span className={styles.popoverLabel}>Dependents</span>
            <span>{popover.dependents}</span>
          </div>
          {popover.insights.length > 0 && (
            <div className={styles.popoverInsights}>
              <span className={styles.popoverLabel}>Insights</span>
              {popover.insights.map((title, i) => (
                <div key={i} className={styles.popoverInsight}>
                  {title}
                </div>
              ))}
            </div>
          )}
          <div className={styles.popoverSummary}>
            {summary && <div className={styles.summaryText}>{summary}</div>}
            <div style={{ display: 'flex', gap: '8px', flexDirection: 'column', marginTop: summary ? '12px' : '0' }}>
              <button 
                className={styles.summarizeBtn} 
                onClick={handleSummarize} 
                disabled={summarizing || summarizingCluster}
              >
                {summarizing ? 'Summarizing...' : 'Summarize File'}
              </button>
              <button 
                className={styles.summarizeBtn} 
                onClick={handleSummarizeCluster} 
                disabled={summarizing || summarizingCluster}
                style={{ background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
              >
                {summarizingCluster ? 'Summarizing...' : 'Summarize Cluster'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── D3 drag behavior ──────────────────────────────────────────
function drag(simulation) {
  return d3
    .drag()
    .on('start', (event, d) => {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x;
      d.fy = d.y;
    })
    .on('drag', (event, d) => {
      d.fx = event.x;
      d.fy = event.y;
    })
    .on('end', (event, d) => {
      if (!event.active) simulation.alphaTarget(0);
      d.fx = null;
      d.fy = null;
    });
}

export default CodebaseMap;
