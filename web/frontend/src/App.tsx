import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'

// ── Types ────────────────────────────────────────────
type View = 'dashboard' | 'memory' | 'graph' | 'findings' | 'settings' | 'workflows' | 'artifacts' | 'causal' | 'negations' | 'memory-dashboard'

const API = 'http://127.0.0.1:8421'

interface BrainStatus {
  total_turns: number; total_sessions: number
  tiers: Record<string, number>; types: Record<string, number>
  storage_bytes: number; last_turn?: { turn: number; ts: string; tier: string }
  daemon_state_path: { exists: boolean }
}
interface StreamItem {
  turn: number; tier: string; type: string; session: string
  ts: string; user: string; assistant: string
  entities: string[]; hash: string; prev_hash: string
}

// ── Icons ────────────────────────────────────────────
const Icons: Record<string, string> = {
  dashboard: '◉',
  memory: '⟐',
  graph: '◎',
  findings: '⚠',
  settings: '⚙',
  workflows: '⇶',
  artifacts: '◈',
  causal: '↻',
  negations: '⊘',
  'memory-dashboard': '⬡',
}

// ── App ──────────────────────────────────────────────
export default function App() {
  const [view, setView] = useState<View>('dashboard')
  const [status, setStatus] = useState<BrainStatus | null>(null)
  const [stream, setStream] = useState<StreamItem[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [daemonHealth, setDaemonHealth] = useState<any>(null)
  const [proxyRunning, setProxyRunning] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const refreshRef = useRef<number>(0)

  // Fetch status
  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/status`)
      const d = await r.json()
      setStatus(d as BrainStatus)
    } catch (e) { console.warn('[mycelium] status:', e) }
  }, [])

  const fetchStream = useCallback(async (q?: string) => {
    try {
      const url = q ? `${API}/api/stream?limit=50&q=${encodeURIComponent(q)}` : `${API}/api/stream?limit=50`
      const r = await fetch(url)
      const d = await r.json()
      setStream(d.items ?? [])
    } catch (e) { console.warn('[mycelium] stream:', e) }
  }, [])

  const fetchDaemon = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/daemon`)
      const d = await r.json()
      setDaemonHealth(d)
    } catch (e) { console.warn('[mycelium] daemon:', e) }
  }, [])

  // Initial load
  useEffect(() => {
    Promise.all([fetchStatus(), fetchStream(), fetchDaemon()]).then(() => setLoading(false))
    const iv = setInterval(() => { fetchStatus() }, 5000)
    return () => clearInterval(iv)
  }, [fetchStatus, fetchStream, fetchDaemon])

  // Search
  const handleSearch = (q: string) => {
    setSearchQuery(q)
    fetchStream(q || undefined)
  }

  // ── Render ────────────────────────────────────────
  return (
    <div className="app-shell">
      <Sidebar view={view} onView={setView} status={status} collapsed={collapsed} onCollapse={setCollapsed} />

      <div className="main">
        <TopBar view={view} status={status} searchQuery={searchQuery} onSearch={handleSearch} />

        <div className="content">
          {loading ? (
            <div className="loading"><div className="spinner" /> Loading brain...</div>
          ) : (
            <>
              {view === 'dashboard' && <DashboardView status={status} stream={stream} daemon={daemonHealth} />}
              {view === 'memory' && <MemoryView searchQuery={searchQuery} />}
              {view === 'graph' && <GraphView />}
              {view === 'findings' && <FindingsView />}
              {view === 'settings' && <SettingsView daemon={daemonHealth} />}
              {view === 'workflows' && <WorkflowsView />}
              {view === 'artifacts' && <ArtifactsView />}
              {view === 'causal' && <CausalView />}
              {view === 'negations' && <NegationsView />}
              {view === 'memory-dashboard' && <MemoryDashboardView />}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Sidebar ─────────────────────────────────────────
interface SidebarProps {
  view: View
  onView: (v: View) => void
  status: BrainStatus | null
  collapsed: boolean
  onCollapse: (v: boolean) => void
}

const sidebarGroups: { label: string; items: { key: View; label: string }[] }[] = [
  {
    label: 'Core',
    items: [
      { key: 'dashboard', label: 'Dashboard' },
      { key: 'memory', label: 'Memory' },
      { key: 'graph', label: 'Graph' },
    ],
  },
  {
    label: 'Analysis',
    items: [
      { key: 'findings', label: 'Findings' },
      { key: 'causal', label: 'Causal' },
      { key: 'negations', label: 'Negations' },
    ],
  },
  {
    label: 'System',
    items: [
      { key: 'workflows', label: 'Workflows' },
      { key: 'artifacts', label: 'Artifacts' },
      { key: 'settings', label: 'Settings' },
    ],
  },
]

function Sidebar({ view, onView, status, collapsed, onCollapse }: SidebarProps) {
  const isHealthy = status?.daemon_state_path?.exists ?? false

  return (
    <nav className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <div className="sidebar-brand">
        🍄 mycelium
        <small>permanent brain</small>
      </div>

      <div className="sidebar-nav">
        {sidebarGroups.map(group => (
          <div key={group.label} className="sidebar-group">
            <div className="sidebar-group-label">{group.label}</div>
            {group.items.map(item => (
              <button
                key={item.key}
                className={`sidebar-btn${view === item.key ? ' active' : ''}`}
                onClick={() => onView(item.key)}
              >
                <span className="icon">{Icons[item.key]}</span>
                <span className="label">{item.label}</span>
              </button>
            ))}
          </div>
        ))}
      </div>

      <div className="sidebar-footer">
        <div className="pulse">
          <div className={`pulse-dot ${isHealthy ? 'green' : 'red'}`} />
          {isHealthy ? 'Daemon active' : 'Daemon offline'}
        </div>
        <div className="pulse">
          <span className="bp-num">{status?.total_turns ?? '?'}</span>
          <span>entries</span>
        </div>
      </div>

      <button className="sidebar-collapse-btn" onClick={() => onCollapse(!collapsed)}>
        {collapsed ? '→' : '←'}
      </button>
    </nav>
  )
}

// ── TopBar ──────────────────────────────────────────
function TopBar({ view, status, searchQuery, onSearch }: {
  view: View; status: BrainStatus | null; searchQuery: string; onSearch: (q: string) => void
}) {
  const titles: Record<View, string> = {
    dashboard: 'Dashboard',
    memory: 'Memory Browser',
    graph: 'Entity Graph',
    findings: 'Findings',
    settings: 'Settings',
    workflows: 'Workflows',
    artifacts: 'Artifacts',
    causal: 'Causal Tracing',
    negations: 'Negations',
    'memory-dashboard': 'Memory Dashboard',
  }

  return (
    <header className="topbar">
      <h2>{titles[view] ?? view}</h2>
      {view === 'memory' && (
        <input
          className="search-input"
          placeholder="Search memory..."
          value={searchQuery}
          onChange={e => onSearch(e.target.value)}
        />
      )}
      <div className="bp">
        <span>🧠</span>
        <span className="bp-num">{status?.total_turns ?? 0}</span>
      </div>
    </header>
  )
}

// ── Dashboard ───────────────────────────────────────
// Thin wrapper — the live dashboard is now the DashboardTimeline component.
// All the chrome (header strip, lane labels, ticks, detail panel) lives there.
import DashboardTimeline from './components/DashboardTimeline'
function DashboardView({ status, stream, daemon }: {
  status: BrainStatus | null; stream: StreamItem[]; daemon: any
}) {
  return <DashboardTimeline status={status} stream={stream} daemon={daemon} />
}

// ── Memory View ─────────────────────────────────────
function MemoryView({ searchQuery }: {
  searchQuery: string;
}) {
  const [items, setItems] = useState<StreamItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(false)
  const limit = 20

  const totalPages = Math.ceil(total / limit)
  const currentPage = page + 1

  const fetchPage = useCallback(async (p: number, q?: string) => {
    setLoading(true)
    try {
      const offset = p * limit
      const base = `${API}/api/stream?limit=${limit}&offset=${offset}`
      const url = q ? `${base}&q=${encodeURIComponent(q)}` : base
      const r = await fetch(url)
      const d = await r.json()
      setItems(d.items ?? [])
      setTotal(d.total ?? 0)
    } catch (e) { console.warn('[mycelium] stream:', e) }
    setLoading(false)
  }, [limit])

  useEffect(() => {
    fetchPage(0, searchQuery || undefined)
    setPage(0)
  }, [searchQuery, fetchPage])

  const goToPage = (p: number) => {
    if (p < 0 || p >= totalPages) return
    setPage(p)
    fetchPage(p, searchQuery || undefined)
  }

  const getPageNumbers = (): (number | 'ellipsis')[] => {
    const maxVisible = 20
    if (totalPages <= maxVisible) {
      return Array.from({ length: totalPages }, (_, i) => i)
    }
    const pages: (number | 'ellipsis')[] = [0]
    if (page <= 8) {
      // Near start: 0 1 2 ... 18 19
      for (let i = 1; i < maxVisible; i++) pages.push(i)
      pages.push('ellipsis')
      pages.push(totalPages - 1)
    } else if (page >= totalPages - 9) {
      // Near end: 0, ..., totalPages-20 ... totalPages-1
      pages.push('ellipsis')
      for (let i = totalPages - maxVisible; i < totalPages; i++) pages.push(i)
    } else {
      // Middle: 0, ..., window around page, ..., last
      pages.push('ellipsis')
      const half = Math.floor((maxVisible - 4) / 2)
      for (let i = page - half; i <= page + half; i++) pages.push(i)
      pages.push('ellipsis')
      pages.push(totalPages - 1)
    }
    return pages
  }

  const [filtersOpen, setFiltersOpen] = useState(false)
  const [tierFilter, setTierFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  const filtered = useMemo(() => {
    let result = items
    if (tierFilter) result = result.filter(e => e.tier === tierFilter)
    if (typeFilter) result = result.filter(e => e.type === typeFilter)
    return result
  }, [items, tierFilter, typeFilter])

  const distinctTiers = [...new Set(items.map(e => e.tier))]
  const distinctTypes = [...new Set(items.map(e => e.type))]

  // Show loading state
  if (loading && items.length === 0) {
    return <div className="loading"><div className="spinner" /> Loading memory...</div>
  }

  return (
    <div className="fade-in">
      <div className="tabs">
        <button className={`tab ${!tierFilter && !typeFilter ? 'active' : ''}`}
          onClick={() => { setTierFilter(''); setTypeFilter('') }}>All</button>
        {distinctTiers.map(t => (
          <button key={t} className={`tab ${tierFilter === t ? 'active' : ''}`}
            onClick={() => setTierFilter(tierFilter === t ? '' : t)}>{t}</button>
        ))}
        <button className="tab" onClick={() => fetchPage(page, searchQuery || undefined)}>⟳ Refresh</button>
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">⟐</div>
          {searchQuery ? `No results for "${searchQuery}"` : 'No entries yet. Talk to Claude Code to build memory.'}
        </div>
      ) : (
        <div className="entry-list">
          {filtered.map(e => (
            <div key={e.turn} className="entry-item">
              <div className="meta">
                <span className={`tier-${e.tier}`}>● {e.tier}</span>
                <span>Turn {e.turn}</span>
                <span>{e.type}</span>
                <span>{e.ts ? new Date(e.ts).toLocaleString() : ''}</span>
                {e.entities?.slice(0, 4).map(ent => (
                  <span key={ent} className="tag">{ent}</span>
                ))}
              </div>
              <div className="user-text">👤 {e.user?.slice(0, 200)}{e.user?.length > 200 ? '...' : ''}</div>
              <div className="ai-text">
                🤖 <span className="truncated">{e.assistant?.slice(0, 200)}{e.assistant?.length > 200 ? '...' : ''}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      <div className="pagination-bar">
        <button className="tab" disabled={page === 0} onClick={() => goToPage(page - 1)}>‹ Prev</button>
        <div className="pagination-info">
          {getPageNumbers().map((p, i) =>
            p === 'ellipsis'
              ? <span key={`e${i}`} className="pagination-ellipsis">…</span>
              : <button
                  key={p}
                  className={`tab ${p === page ? 'active' : ''}`}
                  onClick={() => goToPage(p)}
                >{p + 1}</button>
          )}
        </div>
        <button className="tab" disabled={page >= totalPages - 1} onClick={() => goToPage(page + 1)}>Next ›</button>
      </div>

      <div className="pagination-summary">
        Page {currentPage} of {totalPages || 1} · {total} entries
        {loading && <span className="pagination-loading"> loading…</span>}
      </div>
    </div>
  )
}

// ── Graph View ──────────────────────────────────────
function GraphView() {
  const [data, setData] = useState<{nodes: any[]; links: any[]} | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/api/connections`).then(r => r.json()).then(d => {
      setData(d)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading"><div className="spinner" /> Loading graph...</div>
  if (!data) return <div className="empty-state"><div className="empty-state-icon">◎</div>No graph data</div>

  const sessions = data.nodes.filter((n: any) => n.kind === 'session')
  const entities = data.nodes.filter((n: any) => n.kind === 'entity')
  // Get top entities by link weight
  const entityWeights: Record<string, number> = {}
  data.links.forEach((l: any) => {
    const target = l.target?.replace('entity:', '')
    if (target) entityWeights[target] = (entityWeights[target] || 0) + l.weight
  })
  const topEntities = Object.entries(entityWeights)
    .sort(([,a], [,b]) => (b as number) - (a as number))
    .slice(0, 20)

  return (
    <div className="fade-in">
      {/* Stats */}
      <div className="stats-grid">
        <div className="stat-card cyan">
          <div className="label">Sessions</div>
          <div className="value">{sessions.length}</div>
        </div>
        <div className="stat-card blue">
          <div className="label">Entities</div>
          <div className="value">{entities.length}</div>
        </div>
        <div className="stat-card purple">
          <div className="label">Connections</div>
          <div className="value">{data.links.length}</div>
        </div>
      </div>

      {/* Session → Entity relationships */}
      <div className="card">
        <div className="card-header">
          <span>💬 Sessions</span>
          <span>{sessions.length} sessions</span>
        </div>
        <div className="card-body">
          {sessions.map((sess: any) => {
            const sessionLinks = data.links.filter((l: any) => l.source === sess.id)
            if (sessionLinks.length === 0) return null
            const cleanLabel = sess.label?.replace(/^\{.*device_id.*\}/, sess.id?.split(':')[1]?.slice(0, 20) || sess.label)
            return (
              <div key={sess.id} className="session-item">
                <div className="session-label">
                  <span>{cleanLabel?.slice(0, 40)}{cleanLabel?.length > 40 ? '…' : ''}</span>
                  <span className="session-meta">
                    {sessionLinks.reduce((s: number, l: any) => s + l.weight, 0)} links
                  </span>
                </div>
                <div className="session-tags">
                  {sessionLinks
                    .sort((a: any, b: any) => b.weight - a.weight)
                    .slice(0, 10)
                    .map((link: any) => (
                      <span key={link.target} className="session-tag">
                        {link.target?.replace('entity:', '')}
                        <span className="session-tag-weight">×{link.weight}</span>
                      </span>
                    ))}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Entity Relationship Map */}
      <div className="card">
        <div className="card-header">
          <span>◎ Entity Map</span>
          <span>top entities by connection weight</span>
        </div>
        <div className="card-body">
          <div className="entity-map">
            {topEntities.map(([name, weight]) => (
              <div key={name} className="entity-card">
                <div className="entity-card-name">{name}</div>
                <div className="entity-card-count">
                  {weight} connection{weight !== 1 ? 's' : ''}
                </div>
                <div className="entity-card-related">
                  {data.links
                    .filter(l => (l.target === `entity:${name}` || l.source === `entity:${name}`) && l.kind === 'entity-entity')
                    .slice(0, 4)
                    .map((l: any) => {
                      const related = (l.target === `entity:${name}` ? l.source : l.target)?.replace('entity:', '')
                      return related ? (
                        <span key={related} className="entity-tag">{related}</span>
                      ) : null
                    })}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Findings View ───────────────────────────────────
function FindingsView() {
  const [findings, setFindings] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/api/findings`).then(r => r.json()).then(d => {
      setFindings(d.items ?? [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading"><div className="spinner" /> Loading findings...</div>

  return (
    <div className="fade-in">
      <div className="stats-grid">
        <div className="stat-card red">
          <div className="label">Critical</div>
          <div className="value">{findings.filter(f => f.finding?.severity === 'critical').length}</div>
        </div>
        <div className="stat-card amber">
          <div className="label">High</div>
          <div className="value">{findings.filter(f => f.finding?.severity === 'high').length}</div>
        </div>
        <div className="stat-card">
          <div className="label">Total</div>
          <div className="value">{findings.length}</div>
        </div>
      </div>

      {findings.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">⚠</div>
          No findings recorded yet.
        </div>
      ) : (
        <div className="entry-list">
          {findings.map((f: any) => (
            <div key={f.turn} className="entry-item">
              <div className="meta">
                <span className={
                  f.finding?.severity === 'critical' ? 'tier-S'
                    : f.finding?.severity === 'high' ? 'tier-A'
                    : 'tier-C'
                }>
                  ● {f.finding?.severity ?? 'info'}
                </span>
                <span>{f.finding?.type ?? 'unknown'}</span>
                <span>Turn {f.turn}</span>
                {f.finding?.target && <span>→ {f.finding.target}</span>}
                <span>{f.ts ? new Date(f.ts).toLocaleDateString() : ''}</span>
              </div>
              <div className="user-text">
                👤 {f.user?.slice(0, 150)}{f.user?.length > 150 ? '...' : ''}
              </div>
              <div className="ai-text">
                {f.finding?.detail && <div>📋 {f.finding.detail.slice(0, 200)}</div>}
                {f.finding?.remediation && <div>🔧 {f.finding.remediation.slice(0, 200)}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Settings View ───────────────────────────────────
function SettingsView({ daemon }: { daemon: any }) {
  const [copied, setCopied] = useState<'mcp' | 'proxy' | null>(null)

  const copy = (text: string, label: 'mcp' | 'proxy') => {
    navigator.clipboard.writeText(text)
    setCopied(label)
    setTimeout(() => setCopied(null), 2000)
  }

  return (
    <div className="fade-in">
      {/* ── MCP Setup Tutorial ───────────────────────── */}
      <div className="settings-section">
        <h3>🔌 MCP Server Setup</h3>
        <p>
          The MCP server lets Claude Code query mycelium directly. Install once, works forever.
        </p>

        <div className="setting-card">
          <div className="title">Step 1: Verify MCP binary</div>
          <div className="desc">Make sure the MCP server is installed.</div>
          <div className="code-block">
            ls -lh ~/.local/bin/mycelium-mcp
          </div>
        </div>

        <div className="setting-card">
          <div className="title">Step 2: Add to Claude Code settings</div>
          <div className="desc">
            Add this to <code>~/.claude/settings.json</code>.
            This makes Claude Code spawn the MCP server on every session.
          </div>
          <div className="code-block">
            <button className="copy-btn" onClick={() => copy(mcpJson, 'mcp')}>
              {copied === 'mcp' ? '✓ Copied!' : 'Copy'}
            </button>
{JSON.stringify({
  mcpServers: {
    mycelium: {
      command: '/Users/azfar.naufal/.local/bin/mycelium-mcp',
      args: ['--root', '/Users/azfar.naufal/Documents/mycelium']
    }
  }
}, null, 2)}
          </div>
        </div>

        <div className="setting-card">
          <div className="title">Step 3: Verify it works</div>
          <div className="desc">Restart Claude Code. Try asking: <em>"search mycelium for what we discussed about X"</em></div>
          <div className="code-block">
            {`# Or test from terminal:\necho '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \\\\\n  mycelium-mcp --root ~/Documents/mycelium`}
          </div>
        </div>

        <div className="setting-card">
          <div className="title">✅ Currently registered</div>
          <div className="desc">MCP server is already in your settings.json. It auto-activates on next Claude Code session.</div>
        </div>
      </div>

      {/* ── Proxy Activation ─────────────────────────── */}
      <div className="settings-section">
        <h3>🚀 Inference Proxy</h3>
        <p>
          The proxy auto-logs every Claude Code conversation to mycelium.
        </p>

        <div className="setting-card">
          <div className="title">Start the proxy</div>
          <div className="desc">Run this in a terminal tab (or use launchd for persistence):</div>
          <div className="code-block">
            <button className="copy-btn" onClick={() => copy(proxyCmd, 'proxy')}>
              {copied === 'proxy' ? '✓ Copied!' : 'Copy'}
            </button>
mycelium-proxy --upstream http://localhost:8080 --root ~/Documents/mycelium
          </div>
        </div>

        <div className="setting-card">
          <div className="title">Configure Claude Code</div>
          <div className="desc">Add to <code>~/.claude/settings.json</code> or export before running Claude:</div>
          <div className="code-block">
            export ANTHROPIC_BASE_URL=http://127.0.0.1:8443
claude
          </div>
        </div>

        <div className="setting-card">
          <div className="title">Proxy Status</div>
          <div className="daemon-row">
            <div className={`status-dot ${daemon?.last_assistant_id != null ? 'green' : 'yellow'}`} />
            <div>Daemon</div>
            <span>{daemon?.imports ?? 0} imports</span>
          </div>
          <div className="daemon-row">
            <div className="status-dot yellow" />
            <div>Proxy (check port 8443)</div>
            <span>curl http://127.0.0.1:8443/</span>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Workflows View ───────────────────────────────────
const WF_ICONS: Record<string, string> = {
  passed: '✅', failed: '❌', running: '▶', pending: '☐', stopped: '⏹',
}

function WorkflowsView() {
  const [runs, setRuns] = useState<any[]>([])
  const [selectedRun, setSelectedRun] = useState<any | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/workflow/runs?limit=20`)
      const data = await res.json()
      setRuns(data.runs ?? [])
    } catch (e) { console.warn('[workflows] fetch:', e) }
    setLoading(false)
  }, [])

  useEffect(() => { fetchData(); const iv = setInterval(fetchData, 5000); return () => clearInterval(iv) }, [fetchData])

  const viewRunDetail = async (runId: string) => {
    try {
      const res = await fetch(`${API}/api/workflow/status/${encodeURIComponent(runId)}`)
      setSelectedRun(await res.json())
    } catch (e) { console.warn('[workflows] detail:', e) }
  }

  // Run detail view
  if (selectedRun) {
    const r = selectedRun
    const results = r.step_results ?? []
    const steps = r.steps ?? []
    return (
      <div className="fade-in">
        <div className="content-header">
          <h2>⇶ {r.workflow}</h2>
          <div className="header-actions">
            <button className="btn btn-sm" onClick={() => setSelectedRun(null)}>← Back</button>
          </div>
        </div>

        <div className="wf-detail">
          <div className="wf-detail-header">
            <div>
              <div className="wf-detail-meta">
                Run: {r.id?.slice(0, 20)}… &middot;
                Status: <span className={`wf-status-${r.status}`}>{WF_ICONS[r.status]} {r.status}</span>
                {r.completed_at ? ` · ${new Date(r.completed_at).toLocaleString()}` : ''}
              </div>
            </div>
            <div className={`wf-badge wf-badge-${r.status}`}>{r.status}</div>
          </div>

          {r.error && <div className="wf-error">{r.error}</div>}

          <div className="wf-step-list">
            <div className="wf-step-header">
              <div className="wf-step-status"></div>
              <div className="wf-step-name">Step</div>
              <div className="wf-step-criteria">Exit Criteria</div>
              <div className="wf-step-note">Note</div>
            </div>
            {steps.map((step: any, i: number) => {
              const sr = results[i] ?? { status: i < (r.current_step ?? 0) ? 'passed' : 'pending' }
              return (
                <div key={i} className={`wf-step-row wf-step-${sr.status}`}>
                  <div className="wf-step-status">{WF_ICONS[sr.status] ?? '☐'}</div>
                  <div className="wf-step-name">
                    {step.name}
                    {step.exit_criteria && <div className="wf-step-criteria-text">{step.exit_criteria}</div>}
                  </div>
                  <div className="wf-step-criteria">{step.exit_criteria || '—'}</div>
                  <div className="wf-step-note">{sr.note || ''}</div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    )
  }

  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>

  // Find active run (first running one)
  const activeRun = runs.find(r => r.status === 'running')
  const pastRuns = runs.filter(r => r.status !== 'running')

  return (
    <div className="fade-in">
      <div className="content-header">
        <h2>⇶ Workflows</h2>
      </div>

      {activeRun ? (
        <div className="wf-section">
          <div className="section-heading">▶ Active</div>
          <div className="wf-run-row active" onClick={() => viewRunDetail(activeRun.id)}>
            <div className="wf-run-status"><span className="wf-status-running">▶</span></div>
            <div className="wf-run-info">
              <div className="wf-run-name">{activeRun.workflow_name}</div>
              <div className="wf-run-meta">{activeRun.id?.slice(0, 20)}…</div>
            </div>
            <div className="wf-run-progress">
              <div className="wf-progress-bar">
                <div className="wf-progress-fill" style={{ width: `${(activeRun.current_step / Math.max(activeRun.total_steps, 1)) * 100}%` }} />
              </div>
              <span className="wf-progress-text">{activeRun.current_step}/{activeRun.total_steps}</span>
            </div>
            <div className="wf-run-status-label"><span className="wf-badge wf-badge-running">running</span></div>
          </div>
        </div>
      ) : runs.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">⇶</div>
          No workflows yet. Tell me what you want to do and I'll create one.
        </div>
      ) : null}

      {pastRuns.length > 0 && (
        <div className="wf-section">
          <div className="section-heading">History</div>
          <div className="wf-runs">
            {pastRuns.map((run, i) => (
              <div key={i} className="wf-run-row" onClick={() => viewRunDetail(run.id)}>
                <div className="wf-run-status">
                  <span className={`wf-status-${run.status}`}>{WF_ICONS[run.status] ?? '?'}</span>
                </div>
                <div className="wf-run-info">
                  <div className="wf-run-name">{run.workflow_name}</div>
                  <div className="wf-run-meta">{run.id?.slice(0, 20)}…</div>
                </div>
                <div className="wf-run-progress">
                  <div className="wf-progress-bar">
                    <div className="wf-progress-fill" style={{ width: `${(run.current_step / Math.max(run.total_steps, 1)) * 100}%` }} />
                  </div>
                  <span className="wf-progress-text">{run.current_step}/{run.total_steps}</span>
                </div>
                <div className="wf-run-status-label">
                  <span className={`wf-badge wf-badge-${run.status}`}>{run.status}</span>
                </div>
                <div className="wf-run-time">{run.updated_at?.slice(11, 19) ?? ''}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Artifacts View ───────────────────────────────────
function ArtifactsView() {
  const [stats, setStats] = useState<any>(null)
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')

  useEffect(() => {
    Promise.all([
      fetch(`${API}/api/artifacts/stats`).then(r => r.json()),
      fetch(`${API}/api/artifacts?limit=25`).then(r => r.json()),
    ]).then(([s, d]) => { setStats(s); setItems(d.artifacts ?? []); setLoading(false) })
  }, [])

  const search = async () => {
    if (!q) return
    setLoading(true)
    const r = await fetch(`${API}/api/artifacts/query`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})})
    const d = await r.json()
    setItems(d.results ?? []); setLoading(false)
  }

  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>

  return (
    <div className="fade-in">
      <div className="content-header"><h2>◈ Artifacts</h2></div>

      {stats && (
        <div className="stats-grid">
          <div className="stat-card cyan">
            <div className="label">Total</div>
            <div className="value">{stats.total ?? 0}</div>
          </div>
          <div className="stat-card blue">
            <div className="label">Types</div>
            <div className="value">{stats.by_type ? Object.keys(stats.by_type).length : 0}</div>
            <div className="sub">{stats.by_type ? Object.keys(stats.by_type).join(', ') : '—'}</div>
          </div>
        </div>
      )}

      <div className="recall-bar">
        <input
          className="recall-input"
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="Search artifacts..."
          onKeyDown={e => e.key === 'Enter' && search()}
        />
        <button className="btn btn-sm" onClick={search}>Search</button>
      </div>

      <div className="wf-runs">
        {items.map((a: any, i: number) => (
          <div key={i} className="wf-run-row">
            <div className="wf-run-status"><span className={`wf-badge wf-badge-${a.type === 'output' ? 'done' : 'running'}`}>{a.type || '?'}</span></div>
            <div className="wf-run-info">
              <div className="wf-run-name">{a.name || a.id?.slice(0,20)}</div>
              <div className="wf-run-meta">{a.id} · {a.created_at?.slice(0,10)}</div>
            </div>
          </div>
        ))}
        {items.length === 0 && <div className="empty-state"><div className="empty-state-icon">◈</div>No artifacts</div>}
      </div>
    </div>
  )
}

// ── Causal View ──────────────────────────────────────
function CausalView() {
  const [regressions, setRegressions] = useState<any[]>([])
  const [trace, setTrace] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/api/causal/regressions`).then(r=>r.json()).then(d => { setRegressions(d.regressions ?? []); setLoading(false) }).catch(()=>setLoading(false))
  }, [])

  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>

  return (
    <div className="fade-in">
      <div className="content-header"><h2>↻ Causal Tracing</h2></div>

      {regressions.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">↻</div>
          No regressions detected
        </div>
      ) : (
        <div className="wf-runs">
          {regressions.map((r: any, i: number) => (
            <div key={i} className="wf-run-row" onClick={async () => {
              const res = await fetch(`${API}/api/causal/trace/${r.turn}`)
              setTrace(await res.json())
            }}>
              <div className="wf-run-status">⚠</div>
              <div className="wf-run-info">
                <div className="wf-run-name">Turn {r.turn}</div>
                <div className="wf-run-meta">{r.description?.slice(0,80)}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {trace && (
        <div className="wf-detail">
          <div className="section-heading">Trace</div>
          <div className="code-block">{JSON.stringify(trace, null, 2)}</div>
        </div>
      )}
    </div>
  )
}

// ── Negations View ───────────────────────────────────
function NegationsView() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [approach, setApproach] = useState('')

  useEffect(() => {
    fetch(`${API}/api/negations`).then(r=>r.json()).then(d => { setItems(d.negations ?? []); setLoading(false) }).catch(()=>setLoading(false))
  }, [])

  const filtered = approach ? items.filter((n:any) => n.approach === approach) : items
  const approaches = [...new Set(items.map((n:any) => n.approach).filter(Boolean))] as string[]

  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>

  return (
    <div className="fade-in">
      <div className="content-header"><h2>⊘ Negations</h2></div>

      {approaches.length > 0 && (
        <div className="filter-row">
          <button className={`btn btn-sm${!approach ? ' active' : ''}`} onClick={() => setApproach('')}>All</button>
          {approaches.map(a => (
            <button key={a} className={`btn btn-sm${approach === a ? ' active' : ''}`} onClick={() => setApproach(a)}>{a}</button>
          ))}
        </div>
      )}

      <div className="wf-runs">
        {filtered.map((n: any, i: number) => (
          <div key={i} className="wf-run-row">
            <div className="wf-run-status">⊘</div>
            <div className="wf-run-info">
              <div className="wf-run-name">{n.result || n.user_msg?.slice(0,80)}</div>
              <div className="wf-run-meta">{n.entities && `Entity: ${n.entities}`}{n.approach && ` · ${n.approach}`}</div>
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">⊘</div>
            No negations found
          </div>
        )}
      </div>
    </div>
  )
}

// ── Memory Dashboard View ────────────────────────────
function MemoryDashboardView() {
  const [stats, setStats] = useState<any>(null)
  const [facts, setFacts] = useState<any[]>([])
  const [snapshots, setSnapshots] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [recallQ, setRecallQ] = useState('')
  const [recallRes, setRecallRes] = useState<any[]>([])

  useEffect(() => {
    Promise.all([
      fetch(`${API}/api/memory/stats`).then(r=>r.json()),
      fetch(`${API}/api/memory/facts?limit=20`).then(r=>r.json()),
      fetch(`${API}/api/memory/snapshots`).then(r=>r.json()),
    ]).then(([s, f, sn]) => { setStats(s); setFacts(f.facts??[]); setSnapshots(sn.snapshots??[]); setLoading(false) })
  }, [])

  const recall = async () => {
    if (!recallQ) return
    const r = await fetch(`${API}/api/memory/recall?q=${encodeURIComponent(recallQ)}`)
    const d = await r.json()
    setRecallRes(d.results ?? [])
  }

  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>

  return (
    <div className="fade-in">
      <div className="content-header"><h2>⬡ Memory Dashboard</h2></div>

      {/* Stats Row */}
      <div className="stats-grid">
        <div className="stat-card cyan">
          <div className="label">Facts</div>
          <div className="value">{stats?.total_facts ?? 0}</div>
        </div>
        <div className="stat-card purple">
          <div className="label">Patterns</div>
          <div className="value">{stats?.patterns ?? 0}</div>
        </div>
        <div className="stat-card amber">
          <div className="label">Snapshots</div>
          <div className="value">{stats?.snapshots ?? 0}</div>
        </div>
      </div>

      {/* Recall Search */}
      <div className="recall-bar">
        <input
          className="recall-input"
          value={recallQ}
          onChange={e => setRecallQ(e.target.value)}
          placeholder="Semantic recall..."
          onKeyDown={e => e.key === 'Enter' && recall()}
        />
        <button className="btn btn-sm" onClick={recall}>Recall</button>
      </div>

      {/* Recall Results */}
      {recallRes.length > 0 && (
        <section>
          <div className="section-heading">Recall Results</div>
          <div className="wf-runs">
            {recallRes.map((r: any, i: number) => (
              <div key={i} className="wf-run-row">
                <div className="wf-run-info">
                  <div className="wf-run-name">{r.attribute || r.entity}: {r.value?.slice(0, 80)}</div>
                  <div className="wf-run-meta">confidence: {r.confidence}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recent Facts */}
      <div className="section-heading">Recent Facts</div>
      <div className="wf-runs">
        {facts.map((f: any, i: number) => (
          <div key={i} className="wf-run-row">
            <div className="wf-run-info">
              <span className="wf-run-name">{f.entity}</span> · {f.attribute}: {f.value?.slice(0, 60)}
            </div>
            <div className="wf-run-meta">{f.fact_type} · {f.confidence}</div>
          </div>
        ))}
        {facts.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">⬡</div>
            No facts stored
          </div>
        )}
      </div>

      {/* Snapshots */}
      {snapshots.length > 0 && (
        <section>
          <div className="section-heading">Snapshots</div>
          <div className="wf-runs">
            {snapshots.map((s: any, i: number) => (
              <div key={i} className="wf-run-row">
                <div className="wf-run-info"><span className="wf-run-name">{s.id?.slice(0, 20)}</span></div>
                <div className="wf-run-meta">{s.created_at?.slice(0, 10)}</div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

// ── Constants ────────────────────────────────────────
const mcpJson = JSON.stringify({
  mcpServers: {
    mycelium: {
      command: '/Users/azfar.naufal/.local/bin/mycelium-mcp',
      args: ['--root', '/Users/azfar.naufal/Documents/mycelium']
    }
  }
}, null, 2)

const proxyCmd = 'mycelium-proxy --upstream http://localhost:8080 --root ~/Documents/mycelium'
