/**
 * Mock data for UI verification when VITE_DEV_MOCK=true.
 * Provides fake SSE ingest events and sample insights covering all 6 types.
 */

export const IS_MOCK = import.meta.env.VITE_DEV_MOCK === 'true';

const MOCK_REPO_ID = 'mock-repo-001';
const MOCK_REPO_NAME = 'google/jax';


const ago = (mins) => new Date(Date.now() - mins * 60_000).toISOString();

// ── Sample insights (2 critical, 3 warning, 3 info) ──────────

export const MOCK_INSIGHTS = [
  {
    _id: 'ins-001',
    repo_id: MOCK_REPO_ID,
    type: 'breaking_change_risk',
    severity: 'critical',
    title: 'Breaking change risk: jax/core.py',
    description:
      'jax/core.py has 12 inbound dependents and doc_coverage of 0.14. Any change to this file risks silently breaking 12 downstream modules.',
    affected_files: [
      'jax/core.py',
      'jax/interpreters/xla.py',
      'jax/interpreters/mlir.py',
      'jax/interpreters/pxla.py',
      'jax/_src/dispatch.py',
    ],
    created_at: ago(5),
    resolved: false,
  },
  {
    _id: 'ins-002',
    repo_id: MOCK_REPO_ID,
    type: 'dependency_risk',
    severity: 'critical',
    title: 'High-dependency bottleneck: jax/_src/api.py',
    description:
      'jax/_src/api.py has 14 inbound dependents, making it the most depended-upon file in the repository.',
    affected_files: [
      'jax/_src/api.py',
      'jax/_src/stages.py',
      'jax/_src/sharding.py',
    ],
    created_at: ago(8),
    resolved: false,
  },
  {
    _id: 'ins-003',
    repo_id: MOCK_REPO_ID,
    type: 'stale_docs',
    severity: 'warning',
    title: 'Undocumented public interface: jax/_src/lax/lax.py',
    description:
      'jax/_src/lax/lax.py has doc_coverage 0.18 with 7 inbound dependents. Core primitive operations lack docstrings.',
    affected_files: ['jax/_src/lax/lax.py'],
    created_at: ago(12),
    resolved: false,
  },
  {
    _id: 'ins-004',
    repo_id: MOCK_REPO_ID,
    type: 'complexity_spike',
    severity: 'warning',
    title: 'Complex code region: jax/_src/interpreters/pxla.py',
    description:
      'jax/_src/interpreters/pxla.py has max_complexity of 24. The shard_args function contains deeply nested conditional logic.',
    affected_files: ['jax/_src/interpreters/pxla.py'],
    created_at: ago(15),
    resolved: false,
  },
  {
    _id: 'ins-005',
    repo_id: MOCK_REPO_ID,
    type: 'ownership_gap',
    severity: 'warning',
    title: 'No clear owner: jax/_src/util.py',
    description:
      'jax/_src/util.py has no dominant committer via git-blame and 6 inbound dependents.',
    affected_files: ['jax/_src/util.py'],
    created_at: ago(20),
    resolved: false,
  },
  {
    _id: 'ins-006',
    repo_id: MOCK_REPO_ID,
    type: 'duplicate_logic',
    severity: 'info',
    title: 'Potential duplicate logic: dtype_to_etype',
    description:
      'dtype_to_etype mapping logic is duplicated across jax/_src/dtypes.py and jax/_src/lax/utils.py with near-identical function signatures.',
    affected_files: ['jax/_src/dtypes.py', 'jax/_src/lax/utils.py'],
    created_at: ago(25),
    resolved: false,
  },
  {
    _id: 'ins-007',
    repo_id: MOCK_REPO_ID,
    type: 'stale_docs',
    severity: 'info',
    title: 'Undocumented public interface: jax/_src/tree_util.py',
    description:
      'jax/_src/tree_util.py has doc_coverage 0.27 with 3 inbound dependents.',
    affected_files: ['jax/_src/tree_util.py'],
    created_at: ago(30),
    resolved: false,
  },
  {
    _id: 'ins-008',
    repo_id: MOCK_REPO_ID,
    type: 'dependency_risk',
    severity: 'info',
    title: 'High-dependency bottleneck: jax/_src/config.py',
    description:
      'jax/_src/config.py has 5 inbound dependents. Configuration changes propagate broadly.',
    affected_files: ['jax/_src/config.py', 'jax/_src/lib.py', 'jax/_src/api.py'],
    created_at: ago(35),
    resolved: false,
  },
];

// ── Fake SSE ingest sequence ─────────────────────────────────

const INGEST_STEPS = [
  { delay: 400, event: 'status', data: { phase: 'clone', message: 'Cloning repository metadata...' } },
  { delay: 800, event: 'status', data: { phase: 'clone', message: 'Cloning repository source for parsing...' } },
  { delay: 600, event: 'status', data: { phase: 'parse', message: 'Parsing 247 files...' } },
  { delay: 400, event: 'status', data: { phase: 'parse', message: 'Parsed 100/247 files...' } },
  { delay: 400, event: 'status', data: { phase: 'parse', message: 'Parsed 247/247 files...' } },
  { delay: 500, event: 'status', data: { phase: 'insert', message: 'Writing files metadata to MongoDB MCP...' } },
  { delay: 700, event: 'status', data: { phase: 'insert', message: 'Generating embeddings and writing chunks...' } },
  { delay: 500, event: 'status', data: { phase: 'graph', message: 'Building relationship graph...' } },
  { delay: 400, event: 'status', data: { phase: 'insights', message: 'Generating insights...' } },
  {
    delay: 600,
    event: 'complete',
    data: {
      repo_id: MOCK_REPO_ID,
      repo_name: MOCK_REPO_NAME,
      total_files: 247,
      total_chunks: 1842,
      total_relationships: 634,
      message: 'Ingestion completed successfully.',
    },
  },
];

/**
 * Simulate the SSE ingest flow. Calls onEvent(evt) for each step
 * with realistic timing. Returns an abort function.
 */
export function runMockIngest(onEvent) {
  let cancelled = false;
  let timeoutId = null;

  const run = async () => {
    for (const step of INGEST_STEPS) {
      await new Promise((resolve) => {
        timeoutId = setTimeout(resolve, step.delay);
      });
      if (cancelled) return;
      onEvent({ type: step.event, ...step.data });
    }
  };

  run();

  return () => {
    cancelled = true;
    if (timeoutId) clearTimeout(timeoutId);
  };
}

/**
 * Return mock insights for a given repo, simulating GET /api/insights.
 */
export function getMockInsights(_repoId) {
  return { insights: MOCK_INSIGHTS, total: MOCK_INSIGHTS.length };
}

// ── Mock chat responses ──────────────────────────────────────

const MOCK_RESPONSES = [
  {
    content: `The \`jax/core.py\` module is the central abstraction layer for JAX's tracing system. It defines:

- **\`Trace\`** and **\`Tracer\`** base classes that power all transformations (\`jit\`, \`grad\`, \`vmap\`)
- The **\`Jaxpr\`** intermediate representation used to lower Python functions to XLA HLO
- Primitive registration via \`Primitive.def_impl\` and \`Primitive.def_abstract_eval\`

\`\`\`python
# Example: how a JAX primitive is registered
add_p = Primitive('add')
add_p.def_impl(partial(dispatch.apply_primitive, add_p))
add_p.def_abstract_eval(lambda x, y: raise_to_shaped(x))
\`\`\`

This file has **12 inbound dependents**, making it the highest-risk file for breaking changes.`,
    sources: ['jax/core.py', 'jax/interpreters/xla.py', 'jax/_src/dispatch.py'],
  },
  {
    content: `Here are the files with the highest cyclomatic complexity:

| File | Max Complexity | Key Function |
|------|---------------|--------------|
| \`jax/_src/interpreters/pxla.py\` | 24 | \`shard_args\` |
| \`jax/_src/lax/lax.py\` | 18 | \`_reduce_batch_rule\` |
| \`jax/core.py\` | 15 | \`process_call\` |

The \`shard_args\` function in \`pxla.py\` is the most complex — it handles device mesh mapping with deeply nested conditionals for different sharding strategies.`,
    sources: ['jax/_src/interpreters/pxla.py', 'jax/_src/lax/lax.py', 'jax/core.py'],
  },
  {
    content: `The documentation coverage across the codebase averages **0.34** (34% of public functions have docstrings). The worst offenders are:

1. \`jax/_src/lax/lax.py\` — **0.18** coverage, 7 dependents
2. \`jax/_src/tree_util.py\` — **0.27** coverage, 3 dependents
3. \`jax/core.py\` — **0.14** coverage, 12 dependents

I'd recommend prioritizing \`core.py\` since it has both the lowest coverage and the highest dependent count.`,
    sources: ['jax/_src/lax/lax.py', 'jax/_src/tree_util.py', 'jax/core.py'],
  },
];

let _mockIdx = 0;

/**
 * Simulate a chat response via SSE. Calls onEvent for status + message.
 * Returns an abort function.
 */
export function runMockChat(onEvent) {
  let cancelled = false;
  let timeoutId = null;

  const resp = MOCK_RESPONSES[_mockIdx % MOCK_RESPONSES.length];
  _mockIdx++;

  const run = async () => {
    // Simulate "thinking" delay.
    await new Promise((resolve) => {
      timeoutId = setTimeout(resolve, 1200);
    });
    if (cancelled) return;

    onEvent({
      type: 'message',
      role: 'agent',
      content: resp.content,
      sources: resp.sources,
      message: resp.content,
    });

    await new Promise((resolve) => {
      timeoutId = setTimeout(resolve, 100);
    });
    if (cancelled) return;

    onEvent({ type: 'done', ok: true, message: 'SSE stream closed.' });
  };

  run();

  return () => {
    cancelled = true;
    if (timeoutId) clearTimeout(timeoutId);
  };
}

// ── Mock graph data ──────────────────────────────────────────

const MOCK_FILES = [
  { path: 'jax/core.py', owner: 'mattjj@google.com', doc_coverage: 0.14, size_bytes: 42000 },
  { path: 'jax/_src/api.py', owner: 'mattjj@google.com', doc_coverage: 0.31, size_bytes: 38000 },
  { path: 'jax/_src/dispatch.py', owner: 'yashkatariya@google.com', doc_coverage: 0.22, size_bytes: 15000 },
  { path: 'jax/interpreters/xla.py', owner: 'dougalm@google.com', doc_coverage: 0.19, size_bytes: 28000 },
  { path: 'jax/interpreters/mlir.py', owner: 'dougalm@google.com', doc_coverage: 0.25, size_bytes: 35000 },
  { path: 'jax/interpreters/pxla.py', owner: 'yashkatariya@google.com', doc_coverage: 0.12, size_bytes: 52000 },
  { path: 'jax/_src/lax/lax.py', owner: 'froystig@google.com', doc_coverage: 0.18, size_bytes: 45000 },
  { path: 'jax/_src/lax/utils.py', owner: 'froystig@google.com', doc_coverage: 0.35, size_bytes: 8000 },
  { path: 'jax/_src/util.py', owner: '', doc_coverage: 0.42, size_bytes: 6000 },
  { path: 'jax/_src/dtypes.py', owner: 'jakevdp@google.com', doc_coverage: 0.55, size_bytes: 12000 },
  { path: 'jax/_src/tree_util.py', owner: 'mattjj@google.com', doc_coverage: 0.27, size_bytes: 9000 },
  { path: 'jax/_src/config.py', owner: 'jakevdp@google.com', doc_coverage: 0.60, size_bytes: 5000 },
  { path: 'jax/_src/stages.py', owner: 'dougalm@google.com', doc_coverage: 0.33, size_bytes: 18000 },
  { path: 'jax/_src/sharding.py', owner: 'yashkatariya@google.com', doc_coverage: 0.29, size_bytes: 22000 },
];

const MOCK_RELATIONSHIPS = [
  { from_file: 'jax/_src/api.py', to_file: 'jax/core.py', type: 'imports', weight: 3 },
  { from_file: 'jax/_src/dispatch.py', to_file: 'jax/core.py', type: 'imports', weight: 2 },
  { from_file: 'jax/interpreters/xla.py', to_file: 'jax/core.py', type: 'imports', weight: 4 },
  { from_file: 'jax/interpreters/mlir.py', to_file: 'jax/core.py', type: 'imports', weight: 5 },
  { from_file: 'jax/interpreters/pxla.py', to_file: 'jax/core.py', type: 'imports', weight: 3 },
  { from_file: 'jax/_src/lax/lax.py', to_file: 'jax/core.py', type: 'imports', weight: 6 },
  { from_file: 'jax/_src/tree_util.py', to_file: 'jax/core.py', type: 'imports', weight: 1 },
  { from_file: 'jax/interpreters/pxla.py', to_file: 'jax/interpreters/xla.py', type: 'imports', weight: 2 },
  { from_file: 'jax/interpreters/mlir.py', to_file: 'jax/interpreters/xla.py', type: 'calls', weight: 3 },
  { from_file: 'jax/_src/api.py', to_file: 'jax/_src/dispatch.py', type: 'calls', weight: 4 },
  { from_file: 'jax/_src/api.py', to_file: 'jax/_src/stages.py', type: 'calls', weight: 2 },
  { from_file: 'jax/_src/api.py', to_file: 'jax/_src/sharding.py', type: 'imports', weight: 1 },
  { from_file: 'jax/_src/lax/lax.py', to_file: 'jax/_src/lax/utils.py', type: 'calls', weight: 5 },
  { from_file: 'jax/_src/lax/lax.py', to_file: 'jax/_src/dtypes.py', type: 'imports', weight: 2 },
  { from_file: 'jax/_src/lax/utils.py', to_file: 'jax/_src/dtypes.py', type: 'imports', weight: 1 },
  { from_file: 'jax/_src/dispatch.py', to_file: 'jax/interpreters/xla.py', type: 'calls', weight: 3 },
  { from_file: 'jax/_src/dispatch.py', to_file: 'jax/interpreters/mlir.py', type: 'calls', weight: 2 },
  { from_file: 'jax/interpreters/pxla.py', to_file: 'jax/_src/sharding.py', type: 'imports', weight: 3 },
  { from_file: 'jax/_src/stages.py', to_file: 'jax/core.py', type: 'imports', weight: 2 },
  { from_file: 'jax/_src/config.py', to_file: 'jax/_src/util.py', type: 'imports', weight: 1 },
  { from_file: 'jax/interpreters/pxla.py', to_file: 'jax/interpreters/mlir.py', type: 'extends', weight: 1 },
  { from_file: 'jax/interpreters/xla.py', to_file: 'jax/core.py', type: 'extends', weight: 1 },
];

/**
 * Return mock graph data (files + relationships).
 */
export function getMockGraphData(_repoId) {
  return { files: MOCK_FILES, relationships: MOCK_RELATIONSHIPS };
}


