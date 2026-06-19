import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'

type View = 'dashboard' | 'memory' | 'graph' | 'findings' | 'settings'

const API = 'http://127.0.0.1:8421'

// ── Types ────────────────────────────────────────────
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

// ── Icons (simple inline SVG) ───────────────────────
const Icons = {
  dashboard: <span className="icon">◉</span>,
  memory: <span className="icon">⟐</span>,
  graph: <span className="icon">◎</span>,
  findings: <span className="icon">⚠</span>,
  settings: <span className="icon">⚙</span>,
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
  const refreshRef = useRef<number>(0)

  // Fetch status
  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/status`)
      const d = await r.json()
      setStatus(d as BrainStatus)
    } catch { /* ignore */ }
  }, [])

  const fetchStream = useCallback(async (q?: string) => {
    try {
      const url = q ? `${API}/api/stream?limit=50&q=${encodeURIComponent(q)}` : `${API}/api/stream?limit=50`
      const r = await fetch(url)
      const d = await r.json()
      setStream(d.items ?? [])
    } catch { /* ignore */ }
  }, [])

  const fetchDaemon = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/daemon`)
      const d = await r.json()
      setDaemonHealth(d)
    } catch { /* ignore */ }
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
      <Sidebar view={view} onView={setView} status={status} />

      <div className="main">
        <TopBar view={view} status={status} searchQuery={searchQuery} onSearch={handleSearch} />

        <div className="content">
          {loading ? (
            <div className="loading"><div className="spinner" /> Loading brain...</div>
          ) : (
            <>
              {view === 'dashboard' && <DashboardView status={status} stream={stream} daemon={daemonHealth} />}
              {view === 'memory' && <MemoryView stream={stream} searchQuery={searchQuery} onSearch={handleSearch} onRefresh={() => fetchStream()} />}
              {view === 'graph' && <GraphView />}
              {view === 'findings' && <FindingsView />}
              {view === 'settings' && <SettingsView daemon={daemonHealth} />}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Sidebar ─────────────────────────────────────────
function Sidebar({ view, onView, status }: { view: View; onView: (v: View) => void; status: BrainStatus | null }) {
  const isHealthy = status?.daemon_state_path?.exists ?? false
  return (
    <div className="sidebar">
      <div className="sidebar-brand">
        🍄 mycelium
        <small>permanent brain</small>
      </div>
      <div className="sidebar-nav">
        {(['dashboard', 'memory', 'graph', 'findings', 'settings'] as View[]).map(v => (
          <button key={v} className={`sidebar-btn${view === v ? ' active' : ''}`} onClick={() => onView(v)}>
            {Icons[v]} {v.charAt(0).toUpperCase() + v.slice(1)}
          </button>
        ))}
      </div>
      <div className="sidebar-footer">
        <div className="pulse">
          <div className="pulse-dot" style={{ background: isHealthy ? 'var(--accent-green)' : 'var(--accent-red)' }} />
          {isHealthy ? 'Daemon active' : 'Daemon offline'}
        </div>
        <div className="pulse" style={{ marginTop: 6 }}>
          <span style={{ color: 'var(--accent-cyan)', fontWeight: 600 }}>{status?.total_turns ?? '?'}</span>
          <span>entries</span>
        </div>
      </div>
    </div>
  )
}

// ── TopBar ──────────────────────────────────────────
function TopBar({ view, status, searchQuery, onSearch }: {
  view: View; status: BrainStatus | null; searchQuery: string; onSearch: (q: string) => void
}) {
  const titles: Record<View, string> = {
    dashboard: 'Dashboard', memory: 'Memory Browser', graph: 'Entity Graph',
    findings: 'Findings', settings: 'Settings',
  }
  return (
    <div className="topbar">
      <h2>{titles[view]}</h2>
      {view === 'memory' && (
        <input className="search-input" placeholder="Search memory..." value={searchQuery}
          onChange={e => onSearch(e.target.value)} />
      )}
      <div className="bp">
        <span>🧠</span>
        <span className="bp-num">{status?.total_turns ?? 0}</span>
      </div>
    </div>
  )
}

// ── Dashboard ───────────────────────────────────────
function DashboardView({ status, stream, daemon }: { status: BrainStatus | null; stream: StreamItem[]; daemon: any }) {
  const tiers = status?.tiers ?? {}
  const types = status?.types ?? {}
  const [proxyActive, setProxyActive] = useState(false)

  useEffect(() => {
    const check = async () => {
      try {
        const r = await fetch('http://127.0.0.1:8443/', { signal: AbortSignal.timeout(2000) })
        setProxyActive(r.ok || r.status === 404)
      } catch { setProxyActive(false) }
    }
    check()
    const iv = setInterval(check, 10000)
    return () => clearInterval(iv)
  }, [])

  return (
    <>
      <div className="stats-grid">
        <div className="stat-card cyan">
          <div className="label">Total Turns</div>
          <div className="value">{status?.total_turns ?? 0}</div>
          <div className="sub">{status?.total_sessions ?? 0} sessions</div>
        </div>
        <div className="stat-card blue">
          <div className="label">S-Tier</div>
          <div className="value">{tiers.S ?? 0}</div>
          <div className="sub">{tiers.A ?? 0} A-Tier</div>
        </div>
        <div className="stat-card purple">
          <div className="label">Findings</div>
          <div className="value">{types.finding ?? 0}</div>
          <div className="sub">{types.decision ?? 0} decisions</div>
        </div>
        <div className="stat-card amber">
          <div className="label">Storage</div>
          <div className="value">{(status?.storage_bytes ?? 0) / 1024 > 1024
            ? `${((status?.storage_bytes ?? 0) / 1024 / 1024).toFixed(1)} MB`
            : `${((status?.storage_bytes ?? 0) / 1024).toFixed(0)} KB`}
          </div>
          <div className="sub">brain size</div>
        </div>
      </div>

      {/* Daemon + Proxy Status */}
      <div className="card">
        <div className="card-header">
          <span>⚡ System Health</span>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>auto-refreshes every 5s</span>
        </div>
        <div className="card-body">
          <div className="daemon-row">
            <div className={`status-dot ${daemon?.state?.last_assistant_id != null ? 'green' : 'red'}`} />
            <div style={{ flex: 1 }}>Daemon</div>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {daemon?.state?.imports ?? 0} imports
            </span>
            {daemon?.state?.last_assistant_id != null && (
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                ID: {daemon.state.last_assistant_id}
              </span>
            )}
          </div>
          <div className="daemon-row">
            <div className={`status-dot ${proxyActive ? 'green' : 'red'}`} />
            <div style={{ flex: 1 }}>Proxy (:8443)</div>
            <span style={{ fontSize: 12, color: proxyActive ? 'var(--accent-green)' : 'var(--text-muted)' }}>
              {proxyActive ? 'Active' : 'Offline'}
            </span>
          </div>
          <div className="daemon-row">
            <div className="status-dot green" />
            <div style={{ flex: 1 }}>Health API</div>
            <span style={{ fontSize: 12, color: 'var(--accent-cyan)' }}>
              {API}/api/health
            </span>
          </div>
        </div>
      </div>

      {/* Latest Activity */}
      {stream.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span>🕐 Recent Activity</span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>latest turns</span>
          </div>
          <div className="card-body" style={{ maxHeight: 350, overflow: 'auto' }}>
            {stream.map(e => {
              // Clean proxy garbage from assistant text
              const assistant = e.assistant?.replace(/^data: /, '').replace(/\n/g, ' ').slice(0, 120) || ''
              const user = e.user?.slice(0, 150) || ''
              // Skip raw SSE noise
              if (user.startsWith('data: ') || user.length < 3) return null
              return (
                <div key={e.turn} className="entry-item" style={{ marginBottom: 6 }}>
                  <div className="meta">
                    <span className={`tier-${e.tier}`}>● {e.tier}</span>
                    <span>Turn {e.turn}</span>
                    <span>{e.type}</span>
                    <span style={{ color: 'var(--text-muted)' }}>
                      {e.ts ? new Date(e.ts).toLocaleString() : ''}
                    </span>
                    {e.entities?.slice(0, 3).filter(Boolean).map(ent => (
                      <span key={ent} className="tag">{ent}</span>
                    ))}
                  </div>
                  <div className="user-text">{user}</div>
                  {assistant && !assistant.startsWith('{"message"') && (
                    <div className="ai-text">🤖 {assistant}{e.assistant?.length > 120 ? '...' : ''}</div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </>
  )
}

// ── Memory View ─────────────────────────────────────
function MemoryView({ stream, searchQuery, onSearch, onRefresh }: {
  stream: StreamItem[]; searchQuery: string; onSearch: (q: string) => void; onRefresh: () => void
}) {
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [tierFilter, setTierFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  const filtered = useMemo(() => {
    let items = stream
    if (tierFilter) items = items.filter(e => e.tier === tierFilter)
    if (typeFilter) items = items.filter(e => e.type === typeFilter)
    return items
  }, [stream, tierFilter, typeFilter])

  const distinctTiers = [...new Set(stream.map(e => e.tier))]
  const distinctTypes = [...new Set(stream.map(e => e.type))]

  return (
    <>
      <div className="tabs">
        <button className={`tab ${!tierFilter && !typeFilter ? 'active' : ''}`}
          onClick={() => { setTierFilter(''); setTypeFilter('') }}>All</button>
        {distinctTiers.map(t => (
          <button key={t} className={`tab ${tierFilter === t ? 'active' : ''}`}
            onClick={() => setTierFilter(tierFilter === t ? '' : t)}>{t}</button>
        ))}
        <button className="tab" onClick={onRefresh} style={{ marginLeft: 'auto' }}>⟳ Refresh</button>
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state">
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
                <span style={{ color: 'var(--text-muted)' }}>
                  {e.ts ? new Date(e.ts).toLocaleString() : ''}
                </span>
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
    </>
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
  if (!data) return <div className="empty-state">No graph data</div>

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
    <>
      <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
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
      {sessions.map((sess: any) => {
        const sessionLinks = data.links.filter((l: any) => l.source === sess.id)
        if (sessionLinks.length === 0) return null
        const cleanLabel = sess.label?.replace(/^\{.*device_id.*\}/, sess.id?.split(':')[1]?.slice(0, 20) || sess.label)
        return (
          <div className="card" key={sess.id} style={{ marginBottom: 10 }}>
            <div className="card-header">
              <span>💬 {cleanLabel}</span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {sessionLinks.reduce((s: number, l: any) => s + l.weight, 0)} connections
              </span>
            </div>
            <div className="card-body">
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {sessionLinks
                  .sort((a: any, b: any) => b.weight - a.weight)
                  .slice(0, 8)
                  .map((link: any) => {
                    const label = link.target?.replace('entity:', '')
                    return (
                      <div key={link.target} style={{
                        background: 'var(--bg-active)',
                        border: '1px solid var(--border)',
                        borderRadius: 6,
                        padding: '6px 12px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        fontSize: 12,
                      }}>
                        <span style={{ color: 'var(--accent-cyan)' }}>{label}</span>
                        <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>×{link.weight}</span>
                      </div>
                    )
                  })}
                {sessionLinks.length > 8 && (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', alignSelf: 'center' }}>
                    +{sessionLinks.length - 8} more
                  </div>
                )}
              </div>
            </div>
          </div>
        )
      })}

      {/* Entity Relationship Map */}
      <div className="card">
        <div className="card-header">
          <span>◎ Entity Map — Top Connections</span>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>entities that co-occur together</span>
        </div>
        <div className="card-body">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {topEntities.map(([name, weight]) => (
              <div key={name} style={{
                background: 'var(--gradient-card)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                padding: '12px 16px',
                minWidth: 120,
                flex: '1 0 auto',
              }}>
                <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--accent-blue)' }}>{name}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                  {weight} connection{weight !== 1 ? 's' : ''}
                </div>
                {/* Find related entities */}
                <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                  {data.links
                    .filter(l => (l.target === `entity:${name}` || l.source === `entity:${name}`) && l.kind === 'entity-entity')
                    .slice(0, 4)
                    .map((l: any) => {
                      const related = (l.target === `entity:${name}` ? l.source : l.target)?.replace('entity:', '')
                      return related ? (
                        <span key={related} style={{
                          fontSize: 10, padding: '1px 6px',
                          background: 'var(--bg-hover)', borderRadius: 3,
                          color: 'var(--text-secondary)',
                        }}>{related}</span>
                      ) : null
                    })}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
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
    <>
      <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
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
        <div className="empty-state">No findings recorded yet.</div>
      ) : (
        <div className="entry-list">
          {findings.map((f: any) => (
            <div key={f.turn} className="entry-item">
              <div className="meta">
                <span style={{
                  color: f.finding?.severity === 'critical' ? 'var(--accent-red)'
                    : f.finding?.severity === 'high' ? 'var(--accent-amber)'
                    : 'var(--text-secondary)'
                }}>
                  ● {f.finding?.severity ?? 'info'}
                </span>
                <span>{f.finding?.type ?? 'unknown'}</span>
                <span>Turn {f.turn}</span>
                {f.finding?.target && <span>→ {f.finding.target}</span>}
                <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>
                  {f.ts ? new Date(f.ts).toLocaleDateString() : ''}
                </span>
              </div>
              <div className="user-text">
                👤 {f.user?.slice(0, 150)}{f.user?.length > 150 ? '...' : ''}
              </div>
              <div className="ai-text" style={{ fontSize: 11, marginTop: 4 }}>
                {f.finding?.detail && <div style={{ color: 'var(--text-secondary)' }}>📋 {f.finding.detail.slice(0, 200)}</div>}
                {f.finding?.remediation && <div style={{ color: 'var(--accent-green)', marginTop: 2 }}>🔧 {f.finding.remediation.slice(0, 200)}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
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
    <>
      {/* ── MCP Setup Tutorial ───────────────────────── */}
      <div className="settings-section">
        <h3>🔌 MCP Server Setup</h3>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
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
            Add this to <code style={{ color: 'var(--accent-cyan)' }}>~/.claude/settings.json</code>.
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
            {/* Test command - curly braces escaped for JSX */}
            {`# Or test from terminal:
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \\
  mycelium-mcp --root ~/Documents/mycelium`}
          </div>
        </div>

        <div className="setting-card" style={{ borderColor: 'var(--accent-cyan)' }}>
          <div className="title">✅ Currently registered</div>
          <div className="desc">MCP server is already in your settings.json. It auto-activates on next Claude Code session.</div>
        </div>
      </div>

      {/* ── Proxy Activation ─────────────────────────── */}
      <div className="settings-section">
        <h3>🚀 Inference Proxy</h3>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
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
          <div className="desc">Add to <code style={{ color: 'var(--accent-cyan)' }}>~/.claude/settings.json</code> or export before running Claude:</div>
          <div className="code-block">
            export ANTHROPIC_BASE_URL=http://127.0.0.1:8443
claude
          </div>
        </div>

        <div className="setting-card" style={{ borderColor: 'var(--border-light)' }}>
          <div className="title">Proxy Status</div>
          <div className="daemon-row" style={{ padding: '8px 0' }}>
            <div className={`status-dot ${daemon?.state?.last_assistant_id != null ? 'green' : 'yellow'}`} />
            <div style={{ flex: 1 }}>Daemon</div>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {daemon?.state?.imports ?? 0} imports
            </span>
          </div>
          <div className="daemon-row" style={{ padding: '8px 0' }}>
            <div className="status-dot yellow" />
            <div style={{ flex: 1 }}>Proxy (check port 8443)</div>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              curl http://127.0.0.1:8443/
            </span>
          </div>
        </div>
      </div>
    </>
  )
}

const mcpJson = JSON.stringify({
  mcpServers: {
    mycelium: {
      command: '/Users/azfar.naufal/.local/bin/mycelium-mcp',
      args: ['--root', '/Users/azfar.naufal/Documents/mycelium']
    }
  }
}, null, 2)

const proxyCmd = 'mycelium-proxy --upstream http://localhost:8080 --root ~/Documents/mycelium'
