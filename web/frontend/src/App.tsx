import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'

type View = 'dashboard' | 'memory' | 'graph' | 'findings' | 'settings' | 'workflows' | 'artifacts' | 'causal' | 'negations' | 'memory-dashboard'

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
  workflows: <span className="icon">⇶</span>,
  artifacts: <span className="icon">◈</span>,
  causal: <span className="icon">↻</span>,
  negations: <span className="icon">⊘</span>,
  'memory-dashboard': <span className="icon">⬡</span>,
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
      <Sidebar view={view} onView={setView} status={status} />

      <div className="main">
        <TopBar view={view} status={status} searchQuery={searchQuery} onSearch={handleSearch} />

        <div className="content">
          {loading ? (
            <div className="loading"><div className="spinner" /> Loading brain...</div>
          ) : (
            <>
              {view === 'dashboard' && <DashboardView status={status} stream={stream} daemon={daemonHealth} onView={setView} />}
              {view === 'memory' && <MemoryView stream={stream} searchQuery={searchQuery} onSearch={handleSearch} onRefresh={() => fetchStream()} />}
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
function Sidebar({ view, onView, status }: { view: View; onView: (v: View) => void; status: BrainStatus | null }) {
  const isHealthy = status?.daemon_state_path?.exists ?? false
  return (
    <div className="sidebar">
      <div className="sidebar-brand">
        🍄 mycelium
        <small>permanent brain</small>
      </div>
      <div className="sidebar-nav">
        {(['dashboard', 'memory', 'graph', 'findings', 'artifacts', 'causal', 'negations', 'workflows', 'settings'] as View[]).map(v => (
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
function DashboardView({ status, stream, daemon, onView }: { status: BrainStatus | null; stream: StreamItem[]; daemon: any; onView: (v: View) => void }) {
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
      {activeRun && (
        <div className="wf-section" style={{marginBottom:16}}>
          <div className="wf-run-row active" onClick={() => onView('workflows')} style={{cursor:'pointer'}}>
            <div className="wf-run-status"><span className="wf-status-running">▶</span></div>
            <div className="wf-run-info">
              <div className="wf-run-name">{activeRun.workflow_name}</div>
              <div className="wf-run-meta">{activeRun.current_step}/{activeRun.total_steps} steps</div>
            </div>
            <div className="wf-run-progress">
              <div className="wf-progress-bar"><div className="wf-progress-fill" style={{width:`${(activeRun.current_step/Math.max(activeRun.total_steps,1))*100}%`}} /></div>
            </div>
          </div>
        </div>
      )}

      {/* Stats Cards */}
      <div style={{display:'flex',gap:10,marginBottom:16,flexWrap:'wrap'}}>
        <div className="card" style={{flex:'1 1 140px',minWidth:100}}>
          <div className="card-body" style={{textAlign:'center'}}>
            <div style={{fontSize:24,fontWeight:700,color:'var(--accent-cyan)'}}>{status?.total_turns ?? 0}</div>
            <div style={{fontSize:11,color:'var(--text-muted)',marginTop:2}}>Turns</div>
          </div>
        </div>
        <div className="card" style={{flex:'1 1 140px',minWidth:100}}>
          <div className="card-body" style={{textAlign:'center'}}>
            <div style={{fontSize:24,fontWeight:700,color:'var(--accent-purple)'}}>{status?.total_sessions ?? 0}</div>
            <div style={{fontSize:11,color:'var(--text-muted)',marginTop:2}}>Sessions</div>
          </div>
        </div>
        <div className="card" style={{flex:'1 1 140px',minWidth:100}}>
          <div className="card-body" style={{textAlign:'center'}}>
            <div style={{fontSize:24,fontWeight:700,color:'var(--accent-amber)'}}>{status?.storage_bytes ? (status.storage_bytes/1024).toFixed(0) : 0}KB</div>
            <div style={{fontSize:11,color:'var(--text-muted)',marginTop:2}}>Size</div>
          </div>
        </div>
        <div className="card" style={{flex:'1 1 140px',minWidth:100}}>
          <div className="card-body" style={{textAlign:'center'}}>
            <div style={{fontSize:20,fontWeight:700}}>S <span style={{color:'var(--accent-amber)',fontSize:24}}>{tiers.S||0}</span></div>
            <div style={{fontSize:11,color:'var(--text-muted)',marginTop:2}}>A: {tiers.A||0}  B: {tiers.B||0}</div>
          </div>
        </div>
        <div className="card" style={{flex:'1 1 140px',minWidth:100}}>
          <div className="card-body" style={{textAlign:'center'}}>
            <div style={{fontSize:12,color:'var(--text-muted)',marginBottom:4}}>Daemon</div>
            <div style={{fontSize:20}}>{daemon?.last_assistant_id != null ? '🟢' : '🔴'}</div>
            <div style={{fontSize:11,color:'var(--text-muted)',marginTop:2}}>{daemon?.imports??0} imports</div>
          </div>
        </div>
        <div className="card" style={{flex:'1 1 140px',minWidth:100}}>
          <div className="card-body" style={{textAlign:'center'}}>
            <div style={{fontSize:12,color:'var(--text-muted)',marginBottom:4}}>Proxy</div>
            <div style={{fontSize:20}}>{proxyActive ? '🟢' : '🔴'}</div>
            <div style={{fontSize:11,color:'var(--text-muted)',marginTop:2}}>:8443</div>
          </div>
        </div>
      </div>

      {/* Quick Links Grid */}
      <h3 style={{fontSize:13,color:'var(--text-secondary)',marginBottom:10}}>Sections</h3>
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(150px,1fr))',gap:8,marginBottom:20}}>
        {sections.map(s => (
          <div key={s.key} className="wf-run-row" onClick={() => onView(s.key)} style={{cursor:'pointer',flexDirection:'column',alignItems:'flex-start',gap:4,padding:'12px 14px'}}>
            <div style={{fontSize:18}}>{s.icon}</div>
            <div className="wf-run-name" style={{fontSize:13}}>{s.label}</div>
            <div style={{fontSize:11,color:'var(--text-muted)'}}>{s.desc}</div>
          </div>
        ))}
      </div>

      {/* Recent Activity */}
      <h3 style={{fontSize:13,color:'var(--text-secondary)',marginBottom:10}}>Recent Activity</h3>
      <div className="wf-runs">
        {topStream.map((e, i) => (
          <div key={i} className="wf-run-row" style={{flexDirection:'column',alignItems:'flex-start',gap:4,cursor:'pointer'}} onClick={() => onView('memory')}>
            <div style={{display:'flex',alignItems:'center',gap:8,width:'100%'}}>
              <span className={`tier-${e.tier}`} style={{fontSize:11,fontWeight:600}}>T{e.tier}</span>
              <span style={{fontSize:11,color:'var(--text-muted)'}}>{e.ts?.slice(11,19)}</span>
              <span className="tag" style={{fontSize:10}}>{e.type}</span>
            </div>
            <div className="user-text" style={{fontSize:12,margin:0}}>{e.user?.slice(0,120)}</div>
            <div className="ai-text" style={{fontSize:11,color:'var(--text-secondary)',margin:0}}>{e.assistant?.slice(0,120)}</div>
          </div>
        ))}
      </div>
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
      <div className="card">
        <div className="card-header">
          <span>💬 Sessions</span>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{sessions.length} sessions</span>
        </div>
        <div className="card-body" style={{ maxHeight: 420, overflowY: 'auto', padding: 0 }}>
          {sessions.map((sess: any) => {
            const sessionLinks = data.links.filter((l: any) => l.source === sess.id)
            if (sessionLinks.length === 0) return null
            const cleanLabel = sess.label?.replace(/^\{.*device_id.*\}/, sess.id?.split(':')[1]?.slice(0, 20) || sess.label)
            return (
              <div key={sess.id} style={{
                padding: '12px 18px',
                borderBottom: '1px solid var(--border)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--accent-cyan)' }}>
                    {cleanLabel?.slice(0, 40)}{cleanLabel?.length > 40 ? '…' : ''}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
                    {sessionLinks.reduce((s: number, l: any) => s + l.weight, 0)} links
                  </span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {sessionLinks
                    .sort((a: any, b: any) => b.weight - a.weight)
                    .slice(0, 10)
                    .map((link: any) => (
                      <span key={link.target} style={{
                        background: 'var(--bg-hover)',
                        border: '1px solid var(--border)',
                        borderRadius: 4,
                        padding: '3px 10px',
                        fontSize: 12, color: 'var(--text-secondary)',
                      }}>
                        {link.target?.replace('entity:', '')}
                        <span style={{ color: 'var(--text-muted)', fontSize: 10, marginLeft: 4 }}>×{link.weight}</span>
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
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>top entities by connection weight</span>
        </div>
        <div className="card-body" style={{ maxHeight: 400, overflowY: 'auto' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {topEntities.map(([name, weight]) => (
              <div key={name} style={{
                background: 'var(--gradient-card)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                padding: '12px 14px',
                minWidth: 140,
                flex: '0 1 auto',
              }}>
                <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--accent-blue)' }}>{name}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>
                  {weight} connection{weight !== 1 ? 's' : ''}
                </div>
                <div style={{ marginTop: 5, display: 'flex', flexWrap: 'wrap', gap: 2 }}>
                  {data.links
                    .filter(l => (l.target === `entity:${name}` || l.source === `entity:${name}`) && l.kind === 'entity-entity')
                    .slice(0, 4)
                    .map((l: any) => {
                      const related = (l.target === `entity:${name}` ? l.source : l.target)?.replace('entity:', '')
                      return related ? (
                        <span key={related} style={{
                          fontSize: 10, padding: '1px 5px',
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
            <div className={`status-dot ${daemon?.last_assistant_id != null ? 'green' : 'yellow'}`} />
            <div style={{ flex: 1 }}>Daemon</div>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {daemon?.imports ?? 0} imports
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
      <>
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
      </>
    )
  }

  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>

  // Find active run (first running one)
  const activeRun = runs.find(r => r.status === 'running')
  const pastRuns = runs.filter(r => r.status !== 'running')

  return (
    <>
      <div className="content-header">
        <h2>⇶ Workflows</h2>
      </div>

      {activeRun ? (
        <div className="wf-section">
          <h3 style={{ margin: '0 0 8px', fontSize: 13, color: 'var(--text-secondary)' }}>▶ Active</h3>
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
        <div className="empty-state">No workflows yet. Tell me what you want to do and I'll create one.</div>
      ) : null}

      {pastRuns.length > 0 && (
        <div className="wf-section">
          <h3 style={{ margin: '16px 0 8px', fontSize: 13, color: 'var(--text-secondary)' }}>History</h3>
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
    </>
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
    <>
      <div className="content-header"><h2>◈ Artifacts</h2></div>
      <div style={{display:'flex',gap:12,marginBottom:16}}>
        {stats && <div className="card" style={{flex:1}}><div className="card-body" style={{fontSize:13}}>Total: {stats.total ?? 0}<br/>Types: {stats.by_type?.join(', ') ?? '—'}</div></div>}
      </div>
      <div style={{display:'flex',gap:8,marginBottom:12}}>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search artifacts..." style={{flex:1,padding:'8px 12px',background:'var(--bg-card)',border:'1px solid var(--border)',borderRadius:'var(--radius-sm)',color:'var(--text-primary)',fontSize:13}} onKeyDown={e => e.key==='Enter'&&search()} />
        <button className="btn btn-sm" onClick={search}>Search</button>
      </div>
      <div className="wf-runs">
        {items.map((a: any, i: number) => (
          <div key={i} className="wf-run-row">
            <div className="wf-run-status"><span className={`wf-badge wf-badge-${a.type === 'output' ? 'done' : 'running'}`} style={{fontSize:10}}>{a.type || '?'}</span></div>
            <div className="wf-run-info">
              <div className="wf-run-name">{a.name || a.id?.slice(0,20)}</div>
              <div className="wf-run-meta">{a.id} · {a.created_at?.slice(0,10)}</div>
            </div>
          </div>
        ))}
        {items.length === 0 && <div className="empty-state">No artifacts</div>}
      </div>
    </>
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
    <>
      <div className="content-header"><h2>↻ Causal Tracing</h2></div>
      {regressions.length === 0 ? <div className="empty-state">No regressions detected</div> : (
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
        <div className="wf-detail" style={{marginTop:16}}>
          <h3 style={{fontSize:14,margin:'0 0 8px',color:'var(--accent-amber)'}}>Trace</h3>
          <pre style={{background:'var(--bg-card)',padding:12,borderRadius:'var(--radius-sm)',fontSize:12,color:'var(--text-secondary)',overflow:'auto',maxHeight:300}}>{JSON.stringify(trace,null,2)}</pre>
        </div>
      )}
    </>
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
    <>
      <div className="content-header"><h2>⊘ Negations</h2></div>
      {approaches.length > 0 && (
        <div style={{display:'flex',gap:6,marginBottom:12,flexWrap:'wrap'}}>
          <button className={`btn btn-sm${!approach?' active':''}`} onClick={()=>setApproach('')}>All</button>
          {approaches.map(a => <button key={a} className={`btn btn-sm${approach===a?' active':''}`} onClick={()=>setApproach(a)}>{a}</button>)}
        </div>
      )}
      <div className="wf-runs">
        {filtered.map((n: any, i: number) => (
          <div key={i} className="wf-run-row">
            <div className="wf-run-status">⊘</div>
            <div className="wf-run-info">
              <div className="wf-run-name">{n.value || n.text?.slice(0,60)}</div>
              <div className="wf-run-meta">{n.entity && `Entity: ${n.entity}`}{n.approach && ` · ${n.approach}`}</div>
            </div>
          </div>
        ))}
        {filtered.length === 0 && <div className="empty-state">No negations found</div>}
      </div>
    </>
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
    <>
      <div className="content-header"><h2>⬡ Memory Dashboard</h2></div>
      <div style={{display:'flex',gap:12,marginBottom:16,flexWrap:'wrap'}}>
        <div className="card" style={{flex:1,minWidth:120}}><div className="card-body"><div style={{fontSize:20,fontWeight:600,color:'var(--accent-cyan)'}}>{stats?.total_facts??0}</div><div style={{fontSize:12,color:'var(--text-muted)'}}>Facts</div></div></div>
        <div className="card" style={{flex:1,minWidth:120}}><div className="card-body"><div style={{fontSize:20,fontWeight:600,color:'var(--accent-purple)'}}>{stats?.patterns??0}</div><div style={{fontSize:12,color:'var(--text-muted)'}}>Patterns</div></div></div>
        <div className="card" style={{flex:1,minWidth:120}}><div className="card-body"><div style={{fontSize:20,fontWeight:600,color:'var(--accent-amber)'}}>{stats?.snapshots??0}</div><div style={{fontSize:12,color:'var(--text-muted)'}}>Snapshots</div></div></div>
      </div>
      <div style={{display:'flex',gap:8,marginBottom:12}}>
        <input value={recallQ} onChange={e=>setRecallQ(e.target.value)} placeholder="Semantic recall..." style={{flex:1,padding:'8px 12px',background:'var(--bg-card)',border:'1px solid var(--border)',borderRadius:'var(--radius-sm)',color:'var(--text-primary)',fontSize:13}} onKeyDown={e=>e.key==='Enter'&&recall()} />
        <button className="btn btn-sm" onClick={recall}>Recall</button>
      </div>
      {recallRes.length > 0 && (
        <div style={{marginBottom:16}}>
          <h3 style={{fontSize:13,margin:'0 0 8px',color:'var(--text-secondary)'}}>Recall Results</h3>
          {recallRes.map((r:any,i:number) => (
            <div key={i} className="wf-run-row" style={{fontSize:12}}>
              <div className="wf-run-name">{r.attribute || r.entity}: {r.value?.slice(0,80)}</div>
              <div className="wf-run-meta">confidence: {r.confidence}</div>
            </div>
          ))}
        </div>
      )}
      <h3 style={{fontSize:13,margin:'0 0 8px',color:'var(--text-secondary)'}}>Recent Facts</h3>
      <div className="wf-runs">
        {facts.map((f:any,i:number) => (
          <div key={i} className="wf-run-row" style={{fontSize:12}}>
            <div className="wf-run-info"><span className="wf-run-name">{f.entity}</span> · {f.attribute}: {f.value?.slice(0,60)}</div>
            <div className="wf-run-meta" style={{fontSize:11}}>{f.fact_type} · {f.confidence}</div>
          </div>
        ))}
        {facts.length === 0 && <div className="empty-state">No facts stored</div>}
      </div>
      {snapshots.length > 0 && (
        <>
          <h3 style={{fontSize:13,margin:'16px 0 8px',color:'var(--text-secondary)'}}>Snapshots</h3>
          <div className="wf-runs">
            {snapshots.map((s:any,i:number) => (
              <div key={i} className="wf-run-row" style={{fontSize:12}}>
                <div className="wf-run-info"><span className="wf-run-name">{s.id?.slice(0,20)}</span></div>
                <div className="wf-run-meta">{s.created_at?.slice(0,10)}</div>
              </div>
            ))}
          </div>
        </>
      )}
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
