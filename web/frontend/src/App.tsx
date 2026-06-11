import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'

type View = 'dashboard' | 'stream' | 'recall' | 'graph' | 'vault' | 'findings'

type Status = {
  total_turns: number
  total_sessions: number
  tiers: Record<string, number>
  recent_sessions: Array<{ session: string; last_ts?: string; last_type?: string; last_tier?: string; entities?: string[] }>
  last_turn?: { session?: string; ts?: string; type?: string; tier?: string }
  canonical_runtime: { path: string; exists: boolean }
  source_root: { path: string; exists: boolean }
  daemon_state_path: { path: string; exists: boolean }
  storage_bytes: number
  archived_files: number
  archived_turns_estimate: number
}

type StreamResponse = { total: number; items: Array<any> }
type DaemonResponse = { running: boolean; state: Record<string, any>; health_url: string }
type VerifyResponse = { ok: boolean; output: string; checked_at: string }
type BackupList = { backup_root: string; items: Array<any>; bundles?: Array<any> }
type ActionResult = { ok: boolean; message?: string; data?: any }
type DangerousAction = 'restore' | 'migrate' | null
type GraphData = { ok: boolean; nodes: Array<any>; links: Array<any>; sessions_considered: number; entities_considered: number }
type RecallResponse = { ok?: boolean; query?: string; intent?: string; confidence?: number; summary?: string; state?: any; source_sessions?: Array<any>; related_entities?: Array<any>; items?: Array<any>; thread_card?: { path: string; thread: string } }
type ThreadList = { ok: boolean; thread_root: string; items: Array<any> }

const API = ''

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function ellipse(text: string, n = 180) {
  if (!text) return ''
  return text.length > n ? `${text.slice(0, n)}\u2026` : text
}

async function post(path: string, body: any) {
  return fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json())
}

function countOf(value: any, key: string) {
  const v = value?.[key]
  return Array.isArray(v) ? v.length : 0
}

function summarizeResult(result: ActionResult | null) {
  if (!result) return [] as Array<{ label: string; value: string }>
  const data = result.data || {}
  return [
    result.message ? { label: 'message', value: String(result.message) } : null,
    data.snapshot ? { label: 'snapshot', value: String(data.snapshot) } : null,
    data.target ? { label: 'target', value: String(data.target) } : null,
    data.target_root ? { label: 'target root', value: String(data.target_root) } : null,
    data.bundle_path ? { label: 'bundle', value: String(data.bundle_path) } : null,
    data.source_root ? { label: 'source', value: String(data.source_root) } : null,
    data.verify?.ok !== undefined ? { label: 'verify', value: data.verify.ok ? 'ok' : 'failed' } : null,
    data.manifest?.total_bytes ? { label: 'bytes', value: formatBytes(Number(data.manifest.total_bytes)) } : null,
    data.actions ? { label: 'actions', value: String(countOf(data, 'actions')) } : null,
    data.conflicts ? { label: 'conflicts', value: String(countOf(data, 'conflicts')) } : null,
    data.restored ? { label: 'restored', value: String(countOf(data, 'restored')) } : null,
    data.relinked ? { label: 'relinked', value: String(countOf(data, 'relinked')) } : null,
    data.copied ? { label: 'copied', value: String(countOf(data, 'copied')) } : null,
    data.mappings ? { label: 'mappings', value: String(countOf(data, 'mappings')) } : null,
    data.mismatches ? { label: 'mismatches', value: String(countOf(data, 'mismatches')) } : null,
  ].filter(Boolean) as Array<{ label: string; value: string }>
}

function sessionSummary(detail: any) {
  if (!detail?.items?.length) return { decisions: [], findings: [], actions: [] }
  const decisions = detail.items.filter((item: any) => item.tier === 'S' || item.type === 'decision').slice(-4)
  const findings = detail.items.filter((item: any) => item.type === 'finding').slice(-4)
  const actions = detail.items.filter((item: any) => /todo|fix|build|implement|deploy|verify/i.test(`${item.user} ${item.assistant}`)).slice(-4)
  return { decisions, findings, actions }
}

function graphPositions(nodes: Array<any>) {
  const sessions = nodes.filter(n => n.kind === 'session')
  const entities = nodes.filter(n => n.kind === 'entity')
  const positions: Record<string, { x: number; y: number }> = {}
  const centerX = 420
  const centerY = 250
  const sessionRadius = 180
  const entityRadius = 300
  sessions.forEach((node, idx) => {
    const angle = (Math.PI * 2 * idx) / Math.max(sessions.length, 1)
    positions[node.id] = {
      x: centerX + Math.cos(angle) * sessionRadius,
      y: centerY + Math.sin(angle) * sessionRadius,
    }
  })
  entities.forEach((node, idx) => {
    const angle = (Math.PI * 2 * idx) / Math.max(entities.length, 1)
    positions[node.id] = {
      x: centerX + Math.cos(angle) * entityRadius,
      y: centerY + Math.sin(angle) * entityRadius,
    }
  })
  return positions
}

function shortLabel(text: string, max = 18) {
  return text.length > max ? `${text.slice(0, max - 1)}\u2026` : text
}

export default function App() {
  const [view, setView] = useState<View>('dashboard')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<Status | null>(null)
  const [stream, setStream] = useState<StreamResponse>({ total: 0, items: [] })
  const [daemon, setDaemon] = useState<DaemonResponse | null>(null)
  const [verify, setVerify] = useState<VerifyResponse | null>(null)
  const [backups, setBackups] = useState<BackupList | null>(null)
  const [graph, setGraph] = useState<GraphData | null>(null)
  const [recallQuery, setRecallQuery] = useState('')
  const [recallResult, setRecallResult] = useState<RecallResponse | null>(null)
  const [threads, setThreads] = useState<ThreadList | null>(null)
  const [recallFeedback, setRecallFeedback] = useState<ActionResult | null>(null)
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [sessionDetail, setSessionDetail] = useState<any>(null)
  const [selectedBackupPath, setSelectedBackupPath] = useState('')
  const [targetRoot, setTargetRoot] = useState('')
  const [actionResult, setActionResult] = useState<ActionResult | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmAction, setConfirmAction] = useState<DangerousAction>(null)
  const [confirmText, setConfirmText] = useState('')
  const confirmInputRef = useRef<HTMLInputElement | null>(null)
  // graph interactions
  const [graphZoom, setGraphZoom] = useState(1)
  const [graphPan, setGraphPan] = useState({ x: 0, y: 0 })
  const [graphDragging, setGraphDragging] = useState(false)
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 })
  const [graphFilter, setGraphFilter] = useState('')
  const [hoveredNode, setHoveredNode] = useState<any>(null)
  const [hoverPos, setHoverPos] = useState({ x: 0, y: 0 })
  const svgRef = useRef<SVGSVGElement | null>(null)

  const loadAll = async () => {
    const [s, st, d, b, g] = await Promise.all([
      fetch(`${API}/api/status`).then(r => r.json()),
      fetch(`${API}/api/stream?limit=40&q=${encodeURIComponent(query)}`).then(r => r.json()),
      fetch(`${API}/api/daemon`).then(r => r.json()),
      fetch(`${API}/api/backups`).then(r => r.json()),
      fetch(`${API}/api/connections`).then(r => r.json()),
    ])
    setStatus(s)
    setStream(st)
    setDaemon(d)
    setBackups(b)
    setGraph(g)
    if (!selectedBackupPath && b?.items?.[0]?.path) setSelectedBackupPath(b.items[0].path)
    if (!targetRoot && s?.canonical_runtime?.path) setTargetRoot(s.canonical_runtime.path)
  }

  useEffect(() => {
    loadAll()
  }, [])

  useEffect(() => {
    const t = setTimeout(() => {
      fetch(`${API}/api/stream?limit=40&q=${encodeURIComponent(query)}`).then(r => r.json()).then(setStream)
    }, 250)
    return () => clearTimeout(t)
  }, [query])

  // confirm modal keyboard
  useEffect(() => {
    if (!confirmAction) return
    const timer = window.setTimeout(() => confirmInputRef.current?.focus(), 20)
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setConfirmAction(null)
        setConfirmText('')
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [confirmAction])

  // graph zoom via wheel
  useEffect(() => {
    const svg = svgRef.current
    if (!svg || view !== 'graph') return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const delta = e.deltaY > 0 ? 0.9 : 1.1
      setGraphZoom(z => Math.max(0.3, Math.min(6, z * delta)))
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [view])

  const runVerify = async () => {
    const res = await fetch(`${API}/api/verify`, { method: 'POST' }).then(r => r.json())
    setVerify(res)
    loadAll()
  }

  const createBackup = async () => {
    setBusy('create-backup')
    const res = await fetch(`${API}/api/backups/create`, { method: 'POST' }).then(r => r.json())
    setActionResult(res)
    setBusy(null)
    loadAll()
  }

  const exportLatest = async () => {
    const latest = backups?.items?.[0]
    if (!latest) return
    setBusy('export-latest')
    const res = await post('/api/backups/export', { path: latest.path })
    setActionResult(res)
    setBusy(null)
    loadAll()
  }

  const openSession = async (name: string) => {
    setSelectedSession(name)
    const data = await fetch(`${API}/api/sessions/${encodeURIComponent(name)}`).then(r => r.json())
    setSessionDetail(data)
    setView('stream')
  }

  const runVaultAction = async (kind: string, path: string, body: any) => {
    setBusy(kind)
    const res = await post(path, body)
    setActionResult(res)
    setBusy(null)
    loadAll()
  }

  const runRecall = async (q = recallQuery) => {
    const clean = q.trim()
    if (!clean) return
    setBusy('recall')
    try {
      const res = await fetch(`${API}/api/recall?q=${encodeURIComponent(clean)}&limit=12`).then(r => r.json())
      setRecallResult(res)
      const t = await fetch(`${API}/api/threads`).then(r => r.json())
      setThreads(t)
    } finally {
      setBusy(null)
    }
  }

  const sendRecallFeedback = async (action: 'boost' | 'split') => {
    const q = recallResult?.query || recallQuery
    if (!q) return
    const res = await post('/api/recall/feedback', { query: q, action })
    setRecallFeedback(res)
    await runRecall(q)
  }

  const askConfirm = (kind: DangerousAction) => {
    setConfirmAction(kind)
    setConfirmText('')
  }

  const closeConfirm = () => {
    setConfirmAction(null)
    setConfirmText('')
  }

  const confirmLabel = confirmAction === 'restore' ? 'RESTORE' : confirmAction === 'migrate' ? 'MIGRATE' : ''
  const confirmReady = confirmAction !== null && confirmText.trim() === confirmLabel

  const executeConfirmed = async () => {
    if (!confirmReady) return
    if (confirmAction === 'restore') {
      await runVaultAction('restore', '/api/import/restore', { path: selectedBackupPath, target_root: targetRoot, overwrite: true })
    }
    if (confirmAction === 'migrate') {
      await runVaultAction('migrate-run', '/api/migrate/execute', { target_root: targetRoot, overwrite: true })
    }
    closeConfirm()
  }

  // graph drag handlers
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return
    setDragStart({ x: e.clientX, y: e.clientY })
    setGraphDragging(true)
  }, [])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (graphDragging) {
      const dx = e.clientX - dragStart.x
      const dy = e.clientY - dragStart.y
      setGraphPan(p => ({ x: p.x + dx, y: p.y + dy }))
      setDragStart({ x: e.clientX, y: e.clientY })
    }
  }, [graphDragging, dragStart])

  const handleMouseUp = useCallback(() => {
    setGraphDragging(false)
  }, [])

  const handleNodeHover = useCallback((node: any, e: React.MouseEvent) => {
    const rect = (e.currentTarget as SVGElement).closest('svg')?.getBoundingClientRect()
    if (rect) {
      setHoverPos({ x: e.clientX - rect.left + 20, y: e.clientY - rect.top - 10 })
    }
    setHoveredNode(node)
  }, [])

  const handleNodeLeave = useCallback(() => {
    setHoveredNode(null)
  }, [])

  // filtered graph
  const filteredNodes = useMemo(() => {
    if (!graphFilter) return null
    const f = graphFilter.toLowerCase()
    return graph?.nodes?.filter(n => n.label?.toLowerCase().includes(f)) || []
  }, [graph, graphFilter])

  const graphGroup = useMemo(() => {
    if (!graph?.links?.length || !graph?.nodes?.length) return null
    const positions = graphPositions(graph.nodes)
    const gx = graphPan.x
    const gy = graphPan.y
    const gz = graphZoom
    const isFaded = (id: string) => {
      if (!filteredNodes) return false
      return !filteredNodes.some(n => n.id === id)
    }
    return { positions, gx, gy, gz, isFaded }
  }, [graph, graphPan, graphZoom, filteredNodes])

  const hero = useMemo(() => {
    if (!status) return null
    return (
      <div className="card hero">
        <div className="ribbon" />
        <div className="row space">
          <div>
            <div className="card-title">Brain Pulse</div>
            <div className="metric">{status.total_turns}</div>
            <div className="metric-sub">turns across {status.total_sessions} sessions</div>
          </div>
          <div className="row">
            <button className="action-btn" onClick={runVerify}>Verify integrity</button>
            <button className="action-btn secondary" onClick={createBackup}>Backup now</button>
          </div>
        </div>
        <div className="chips">
          <span className="chip mono">runtime {status.canonical_runtime.path}</span>
          <span className="chip mono">source {status.source_root.path}</span>
          <span className="chip">archive {status.archived_files} file(s)</span>
          <span className="chip">storage {formatBytes(status.storage_bytes)}</span>
        </div>
      </div>
    )
  }, [status])

  const resultSummary = summarizeResult(actionResult)
  const latestBackup = backups?.items?.[0]
  const sessionMeta = sessionSummary(sessionDetail)

  return (
    <>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">Mycelium</div>
          <div className="brand-sub">memory observatory
            <br />clean local continuity layer
          </div>
          <div className="nav-list">
            {(['dashboard', 'stream', 'recall', 'graph', 'vault', 'findings'] as View[]).map(v => (
              <button key={v} className={`nav-btn ${view === v ? 'active' : ''}`} onClick={() => setView(v)}>{v}</button>
            ))}
          </div>
        </aside>
        <main className="main">
          <div className="topbar">
            <input className="search" placeholder="Search memory, entities, sessions..." value={query} onChange={e => setQuery(e.target.value)} />
            <div className="pill">daemon {daemon?.running ? 'on' : 'off'}</div>
            <div className="pill">backups {backups?.items?.length ?? 0}</div>
          </div>

          {view === 'dashboard' && (
            <div className="grid">
              {hero}
              <div className="grid cols-4">
                <div className="card"><div className="card-title">S tier</div><div className="metric">{status?.tiers?.S ?? 0}</div><div className="metric-sub">critical / decisions</div></div>
                <div className="card"><div className="card-title">A tier</div><div className="metric">{status?.tiers?.A ?? 0}</div><div className="metric-sub">ideas / important</div></div>
                <div className="card"><div className="card-title">B tier</div><div className="metric">{status?.tiers?.B ?? 0}</div><div className="metric-sub">normal memory flow</div></div>
                <div className="card"><div className="card-title">C tier</div><div className="metric">{status?.tiers?.C ?? 0}</div><div className="metric-sub">noise / pruned</div></div>
              </div>
              <div className="grid cols-2">
                <div className="card">
                  <div className="card-title">Recent sessions</div>
                  <div className="session-list">
                    {status?.recent_sessions?.map(s => (
                      <div className="session-item" key={s.session}>
                        <div className="row space">
                          <strong>{s.session}</strong>
                          <button className="action-btn secondary" onClick={() => openSession(s.session)}>open</button>
                        </div>
                        <div className="muted">{s.last_ts} &middot; {s.last_type} &middot; tier {s.last_tier}</div>
                        <div className="chips">{(s.entities || []).map(ent => <span className="chip" key={ent}>{ent}</span>)}</div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="card">
                  <div className="card-title">Integrity / daemon</div>
                  <div className="detail-list">
                    <div><strong>Last turn:</strong> <span className="muted">{status?.last_turn?.session} &middot; {status?.last_turn?.ts}</span></div>
                    <div><strong>Daemon state:</strong> <span className="muted mono">{status?.daemon_state_path.path}</span></div>
                    <div><strong>Health URL:</strong> <span className="muted mono">{daemon?.health_url}</span></div>
                    <div><strong>Imports:</strong> <span className="muted">{daemon?.state?.imports ?? 0}</span></div>
                    <div><strong>Last assistant id:</strong> <span className="muted">{daemon?.state?.last_assistant_id ?? 0}</span></div>
                  </div>
                  {verify && <div className="pre" style={{ marginTop: 16 }}>{verify.output}</div>}
                </div>
              </div>
            </div>
          )}

          {view === 'stream' && (
            <div className="grid cols-2">
              <div className="card">
                <div className="card-title">Memory stream</div>
                <div className="muted" style={{ marginBottom: 14 }}>{stream.total} matching turns</div>
                <div className="stream-list">
                  {stream.items.map(item => (
                    <div className={`stream-item tier-${item.tier}`} key={`${item.turn}-${item.hash}`}>
                      <div className="row space">
                        <strong>{item.session}</strong>
                        <span className="muted">#{item.turn} &middot; {item.type} &middot; {item.ts}</span>
                      </div>
                      <div style={{ marginTop: 10 }}><span className="muted">User:</span> {ellipse(item.user || '', 160)}</div>
                      <div style={{ marginTop: 8 }}><span className="muted">Assistant:</span> {ellipse(item.assistant || '', 180)}</div>
                      <div className="chips">{(item.entities || []).map((ent: string) => <span className="chip" key={ent}>{ent}</span>)}</div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="card">
                <div className="card-title">Session inspector</div>
                {selectedSession && sessionDetail ? (
                  <div className="grid">
                    <div className="session-hero">
                      <div>
                        <div className="metric" style={{ fontSize: 24 }}>{selectedSession}</div>
                        <div className="metric-sub">{sessionDetail.total} turn(s) &middot; {sessionDetail.first_ts} &rarr; {sessionDetail.last_ts}</div>
                      </div>
                      <div className="chips">
                        {Object.entries(sessionDetail.tiers || {}).map(([tier, count]) => <span className="chip" key={tier}>tier {tier} &middot; {String(count)}</span>)}
                      </div>
                    </div>
                    <div className="grid cols-2">
                      <div className="result-panel">
                        <div className="result-label">Top entities</div>
                        <div className="chips">{(sessionDetail.entities || []).slice(0, 8).map((ent: any) => <span className="chip" key={ent.name}>{ent.name} &middot; {ent.count}</span>)}</div>
                      </div>
                      <div className="result-panel">
                        <div className="result-label">Types</div>
                        <div className="chips">{Object.entries(sessionDetail.types || {}).map(([kind, count]) => <span className="chip" key={kind}>{kind} &middot; {String(count)}</span>)}</div>
                      </div>
                    </div>
                    <div className="grid cols-3">
                      <div className="result-panel">
                        <div className="result-label">Decisions</div>
                        <div className="detail-list">{sessionMeta.decisions.length ? sessionMeta.decisions.map((item: any) => <div key={item.hash} className="mini-note">{ellipse(item.assistant || item.user || '', 120)}</div>) : <div className="muted">none</div>}</div>
                      </div>
                      <div className="result-panel">
                        <div className="result-label">Findings</div>
                        <div className="detail-list">{sessionMeta.findings.length ? sessionMeta.findings.map((item: any) => <div key={item.hash} className="mini-note">{item.finding?.type || item.type} &middot; {item.finding?.severity || item.tier}</div>) : <div className="muted">none</div>}</div>
                      </div>
                      <div className="result-panel">
                        <div className="result-label">Actions</div>
                        <div className="detail-list">{sessionMeta.actions.length ? sessionMeta.actions.map((item: any) => <div key={item.hash} className="mini-note">{ellipse(item.user || item.assistant || '', 120)}</div>) : <div className="muted">none</div>}</div>
                      </div>
                    </div>
                    <div className="detail-list">
                      {(sessionDetail.items || []).slice(-6).map((item: any) => (
                        <div className={`stream-item tier-${item.tier}`} key={item.hash}>
                          <div className="row space">
                            <strong>{item.type}</strong>
                            <span className="muted">#{item.turn} &middot; {item.ts}</span>
                          </div>
                          <div style={{ marginTop: 8 }}><span className="muted">User:</span> {ellipse(item.user || '', 150)}</div>
                          <div style={{ marginTop: 8 }}><span className="muted">Assistant:</span> {ellipse(item.assistant || '', 170)}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="muted">Pick a recent session from dashboard.</div>
                )}
              </div>
            </div>
          )}

          {view === 'recall' && (
            <div className="grid cols-2">
              <div className="card recall-card">
                <div className="card-title">Recall panel</div>
                <div className="muted">Ask: continue / remember / last context. Creates thread cards in ~/.hermes/myceliumd/threads/.</div>
                <div className="row" style={{ marginTop: 16 }}>
                  <input className="search" placeholder="continue mycelium recall" value={recallQuery} onChange={e => setRecallQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && runRecall()} />
                  <button className="action-btn" disabled={busy === 'recall'} onClick={() => runRecall()}>{busy === 'recall' ? 'recalling...' : 'Recall'}</button>
                </div>
                {recallResult?.ok && (
                  <div className="recall-result">
                    <div className="result-banner ok">
                      <strong>{recallResult.intent} · confidence {recallResult.confidence}</strong>
                      <span>{recallResult.summary}</span>
                    </div>
                    {recallResult.thread_card && <div className="chip mono">thread {recallResult.thread_card.path}</div>}
                    <RecallState state={recallResult.state} />
                    <div className="row">
                      <button className="action-btn secondary" onClick={() => sendRecallFeedback('boost')}>yes that's it → boost</button>
                      <button className="action-btn secondary" onClick={() => sendRecallFeedback('split')}>no → split</button>
                    </div>
                    {recallFeedback && <div className={`result-banner ${recallFeedback.ok ? 'ok' : 'bad'}`}><strong>feedback</strong><span>{recallFeedback.message || (recallFeedback.ok ? 'saved' : 'failed')}</span></div>}
                    <div className="result-panel">
                      <div className="result-label">Sources</div>
                      <div className="detail-list">{(recallResult.source_sessions || []).slice(0, 6).map(src => <div key={`${src.session}-${src.turn}-${src.hash}`} className="mini-note"><strong>{src.session}</strong> turn {src.turn} · score {src.score}</div>)}</div>
                    </div>
                  </div>
                )}
                {recallResult && !recallResult.ok && <div className="result-banner bad"><strong>Recall failed</strong><span>{recallResult.summary || 'query required'}</span></div>}
              </div>
              <div className="card">
                <div className="card-title">Thread cards</div>
                <div className="muted mono">{threads?.thread_root || '~/.hermes/myceliumd/threads'}</div>
                <div className="session-list" style={{ marginTop: 14 }}>
                  {(threads?.items || []).map(item => (
                    <div className="session-item" key={item.path}>
                      <div className="row space"><strong>{item.title}</strong><span className="muted">{item.updated}</span></div>
                      <div className="mono muted">{item.path}</div>
                      <div style={{ marginTop: 8 }}>{ellipse(item.preview || '', 220)}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {view === 'graph' && (
            <div className="grid cols-2">
              <div className="card graph-card" style={{ position: 'relative' }}>
                <div className="row space">
                  <div>
                    <div className="card-title">Branch / connections</div>
                    <div className="muted">session &harr; entity continuity map</div>
                  </div>
                  <div className="chips">
                    <span className="chip">sessions {graph?.sessions_considered ?? 0}</span>
                    <span className="chip">entities {graph?.entities_considered ?? 0}</span>
                    <span className="chip">zoom {graphZoom.toFixed(1)}x</span>
                  </div>
                </div>
                <div className="row space">
                  <input className="graph-filter-input" placeholder="Filter nodes&hellip;" value={graphFilter} onChange={e => setGraphFilter(e.target.value)} />
                  <div className="graph-zoom-row">
                    <button className="graph-zoom-btn" onClick={() => setGraphZoom(z => Math.max(0.3, z / 1.3))}>Zoom out</button>
                    <button className="graph-zoom-btn" onClick={() => setGraphZoom(1)}>Reset</button>
                    <button className="graph-zoom-btn" onClick={() => setGraphZoom(z => Math.min(6, z * 1.3))}>Zoom in</button>
                  </div>
                </div>
                <svg ref={svgRef} viewBox="0 0 840 500" className="graph-svg"
                  onMouseDown={handleMouseDown}
                  onMouseMove={handleMouseMove}
                  onMouseUp={handleMouseUp}
                  onMouseLeave={handleMouseUp}
                >
                  {graphGroup && (
                    <g transform={`translate(${graphGroup.gx}, ${graphGroup.gy}) scale(${graphGroup.gz})`}>
                      {graph?.links?.map((link, idx) => {
                        const a = graphGroup.positions[link.source]
                        const b = graphGroup.positions[link.target]
                        if (!a || !b) return null
                        const isHighlighted = graphFilter && (graphGroup.isFaded(link.source) || graphGroup.isFaded(link.target))
                        return <line key={idx} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                          className={`graph-link ${link.kind}${!isHighlighted ? ' highlight' : ''}`}
                          strokeWidth={Math.min(5, 1 + link.weight * 0.7)} />
                      })}
                      {graph?.nodes?.map((node) => {
                        const p = graphGroup.positions[node.id]
                        if (!p) return null
                        const r = node.kind === 'session' ? 24 : 14 + Math.min(10, node.weight)
                        const faded = graphGroup.isFaded(node.id)
                        return (
                          <g key={node.id} transform={`translate(${p.x}, ${p.y})`}
                            onMouseEnter={(e) => handleNodeHover(node, e)}
                            onMouseMove={(e) => handleNodeHover(node, e)}
                            onMouseLeave={handleNodeLeave}
                          >
                            <title>{node.label}</title>
                            <circle r={r} className={`graph-node ${node.kind}${faded ? ' faded' : ''}`}
                              onClick={() => node.kind === 'session' && openSession(node.label)} />
                            <text y={r + 18} textAnchor="middle" className={`graph-label${faded ? '' : ''}`}
                              style={{ opacity: faded ? 0.3 : 1 }}>{shortLabel(node.label)}</text>
                          </g>
                        )
                      })}
                    </g>
                  )}
                </svg>
                {hoveredNode && (
                  <div className="graph-tooltip" style={{ left: hoverPos.x, top: hoverPos.y }}>
                    <div className="tooltip-head">{hoveredNode.label}</div>
                    <div className="tooltip-detail">{hoveredNode.kind === 'session' ? 'session' : 'entity'} &middot; weight {hoveredNode.weight}</div>
                    {hoveredNode.kind === 'session' && (
                      <div className="tooltip-detail">Click to open inspector</div>
                    )}
                  </div>
                )}
              </div>
              <div className="card">
                <div className="card-title">Connection notes</div>
                <div className="detail-list">
                  <div className="warning-box">
                    <strong>How to read</strong>
                    <div className="muted">inner ring sessions, outer ring entities. larger circle = more connections. zoom/pan/drag to explore.</div>
                  </div>
                  <div className="result-panel">
                    <div className="result-label">Top session nodes</div>
                    <div className="detail-list">
                      {(graph?.nodes || []).filter((n: any) => n.kind === 'session').slice(0, 6).map((node: any) => (
                        <div key={node.id} className="row space"><span>{node.label}</span><span className="muted">weight {node.weight}</span></div>
                      ))}
                    </div>
                  </div>
                  <div className="result-panel">
                    <div className="result-label">Recurring entities</div>
                    <div className="chips">{(graph?.nodes || []).filter((n: any) => n.kind === 'entity').slice(0, 12).map((node: any) => <span className="chip" key={node.id}>{node.label}</span>)}</div>
                  </div>
                  <div className="muted">tip: scroll to zoom, drag to pan, hover nodes for detail.</div>
                </div>
              </div>
            </div>
          )}

          {view === 'vault' && (
            <div className="grid">
              <div className="grid cols-2">
                <div className="card">
                  <div className="card-title">Vault summary</div>
                  <div className="detail-list">
                    <div><strong>Canonical runtime</strong><div className="mono muted">{status?.canonical_runtime.path}</div></div>
                    <div><strong>Backups root</strong><div className="mono muted">{backups?.backup_root}</div></div>
                    <div><strong>Archive state</strong><div className="muted">{status?.archived_turns_estimate} archived turns est.</div></div>
                    <div><strong>Why this matters</strong><div className="muted">same brain, separate web surface, runs alongside `myceliumd`</div></div>
                  </div>
                  <div className="row" style={{ marginTop: 18 }}>
                    <button className="action-btn" disabled={busy !== null} onClick={createBackup}>
                      {busy === 'create-backup' ? 'backing up...' : 'Backup now'}
                    </button>
                    <button className="action-btn secondary" disabled={busy !== null} onClick={exportLatest}>
                      {busy === 'export-latest' ? 'exporting...' : 'Export latest'}
                    </button>
                  </div>
                </div>
                <div className="card">
                  <div className="card-title">Snapshot history</div>
                  <div className="session-list">
                    {backups?.items?.map(item => (
                      <div className={`session-item ${selectedBackupPath === item.path ? 'selected' : ''}`} key={item.path}>
                        <div className="row space">
                          <strong>{item.name}</strong>
                          <button className="action-btn secondary" onClick={() => setSelectedBackupPath(item.path)}>select</button>
                        </div>
                        <div className="muted">{item.created_at}</div>
                        <div className="mono muted">{item.path}</div>
                        <div className="muted">{formatBytes(item.total_bytes || 0)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="grid cols-2">
                <div className="card">
                  <div className="card-title">Restore / import</div>
                  <div className="detail-list">
                    <label>
                      <div className="muted">Snapshot or bundle path</div>
                      <input className="search mono" value={selectedBackupPath} onChange={e => setSelectedBackupPath(e.target.value)} />
                    </label>
                    <label>
                      <div className="muted">Target root</div>
                      <input className="search mono" value={targetRoot} onChange={e => setTargetRoot(e.target.value)} />
                    </label>
                    <div className="warning-box">
                      <strong>Safe path</strong>
                      <div className="muted">verify backup &rarr; dry-run import &rarr; restore only after review</div>
                    </div>
                  </div>
                  <div className="row" style={{ marginTop: 18 }}>
                    <button className="action-btn secondary" disabled={busy !== null} onClick={() => runVaultAction('verify-backup', '/api/backups/verify', { path: selectedBackupPath })}>
                      {busy === 'verify-backup' ? 'verifying...' : 'Verify backup'}
                    </button>
                    <button className="action-btn secondary" disabled={busy !== null} onClick={() => runVaultAction('import-dry', '/api/import/dry-run', { path: selectedBackupPath, target_root: targetRoot })}>
                      {busy === 'import-dry' ? 'dry-running...' : 'Dry-run import'}
                    </button>
                    <button className="action-btn danger" disabled={busy !== null} onClick={() => askConfirm('restore')}>Restore now</button>
                  </div>
                </div>
                <div className="card">
                  <div className="card-title">Migrate runtime</div>
                  <div className="detail-list">
                    <label>
                      <div className="muted">New runtime root</div>
                      <input className="search mono" value={targetRoot} onChange={e => setTargetRoot(e.target.value)} />
                    </label>
                    <div className="warning-box">
                      <strong>Migration flow</strong>
                      <div className="muted">safety snapshot first &rarr; copy runtime &rarr; relink source surface</div>
                    </div>
                  </div>
                  <div className="row" style={{ marginTop: 18 }}>
                    <button className="action-btn secondary" disabled={busy !== null} onClick={() => runVaultAction('migrate-dry', '/api/migrate/dry-run', { target_root: targetRoot })}>
                      {busy === 'migrate-dry' ? 'dry-running...' : 'Dry-run migrate'}
                    </button>
                    <button className="action-btn danger" disabled={busy !== null} onClick={() => askConfirm('migrate')}>Migrate now</button>
                  </div>
                </div>
              </div>

              <div className="card">
                <div className="card-title">Vault action result</div>
                {busy ? (
                  <div className="muted">&rarr; running {busy}&hellip;</div>
                ) : actionResult ? (
                  <div className="result-grid">
                    <div className={`result-banner ${actionResult.ok ? 'ok' : 'bad'}`}>
                      <strong>{actionResult.ok ? 'Action complete' : 'Action needs attention'}</strong>
                      <span>{actionResult.message || 'no message'}</span>
                    </div>
                    {resultSummary.length > 0 && (
                      <div className="result-stat-grid">
                        {resultSummary.map(item => (
                          <div className="result-stat" key={item.label}>
                            <div className="result-label">{item.label}</div>
                            <div className="result-value mono">{item.value}</div>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="grid cols-2">
                      <div className="result-panel">
                        <div className="result-label">Selected backup</div>
                        <div className="mono muted">{selectedBackupPath || latestBackup?.path || 'none'}</div>
                      </div>
                      <div className="result-panel">
                        <div className="result-label">Target root</div>
                        <div className="mono muted">{targetRoot || 'none'}</div>
                      </div>
                    </div>
                    <details className="details-card">
                      <summary>Raw result JSON</summary>
                      <div className="pre">{JSON.stringify(actionResult, null, 2)}</div>
                    </details>
                  </div>
                ) : (
                  <div className="muted">Select a backup, then verify / dry-run / restore / migrate.</div>
                )}
              </div>
            </div>
          )}

          {view === 'findings' && (
            <FindingsView />
          )}
        </main>
      </div>

      {confirmAction && (
        <div className="modal-backdrop" onClick={closeConfirm}>
          <div className="modal-shell" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="confirm-title">
            <div className="card-title">Danger zone</div>
            <div id="confirm-title" className="confirm-head">{confirmAction === 'restore' ? 'Confirm restore' : 'Confirm migrate'}</div>
            <div className="muted">{confirmAction === 'restore' ? 'This will overwrite target content with the selected backup. This operation cannot be undone.' : 'This will create a safety snapshot, copy runtime data, then rewrite runtime links. Proceed only after dry-run review.'}</div>
            <div className="warning-box danger-card" style={{ border: '1px solid rgba(255, 138, 122, 0.22)' }}>
              <strong style={{ color: 'var(--ember)' }}>&#9888; Review before continue</strong>
              <div className="muted">
                {confirmAction === 'restore'
                  ? `Backup: ${selectedBackupPath || latestBackup?.path || 'none'}`
                  : `Target: ${targetRoot || 'none'}`
                }
              </div>
            </div>
            <div className="grid cols-2">
              <div className="result-panel">
                <div className="result-label">Source</div>
                <div className="mono muted">{confirmAction === 'restore' ? (selectedBackupPath || 'none') : status?.canonical_runtime.path}</div>
              </div>
              <div className="result-panel">
                <div className="result-label">Target</div>
                <div className="mono muted">{confirmAction === 'restore' ? (targetRoot || status?.canonical_runtime.path) : (targetRoot || 'none')}</div>
              </div>
            </div>
            {actionResult && !actionResult.ok && (
              <div className="result-banner bad">
                <strong>Last action had issues</strong>
                <span>{actionResult.message}</span>
              </div>
            )}
            <div className="warning-box">
              <strong>Type to confirm</strong>
              <div className="muted">Type <span className="mono">{confirmLabel}</span> below. Esc or backdrop cancels.</div>
            </div>
            <input
              ref={confirmInputRef}
              className="search mono"
              value={confirmText}
              onChange={e => setConfirmText(e.target.value)}
              placeholder={`type ${confirmLabel}`}
            />
            <div className="row space">
              <button className="action-btn secondary" disabled={busy !== null} onClick={closeConfirm}>Cancel</button>
              <button className="action-btn danger" disabled={!confirmReady || busy !== null} onClick={executeConfirmed}>
                {busy && confirmReady ? 'executing...' : confirmAction === 'restore' ? 'Confirm restore' : 'Confirm migrate'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function RecallState({ state }: { state: any }) {
  if (!state) return null
  const sections = [
    ['Goal', state.goal ? [{ text: state.goal }] : []],
    ['Where left off', state.where_left_off ? [{ text: state.where_left_off }] : []],
    ['Decisions', state.decisions || []],
    ['Next steps', state.next_steps || []],
    ['Open questions', state.open_questions || []],
    ['Blockers', state.blockers || []],
  ] as Array<[string, Array<any>]>
  return (
    <div className="grid cols-2">
      {sections.map(([label, items]) => (
        <div className="result-panel" key={label}>
          <div className="result-label">{label}</div>
          <div className="detail-list">{items.length ? items.slice(0, 5).map((item, idx) => <div className="mini-note" key={idx}>{ellipse(item.text || '', 220)}</div>) : <div className="muted">none</div>}</div>
        </div>
      ))}
      <div className="result-panel">
        <div className="result-label">Files touched</div>
        <div className="detail-list">{(state.files_touched || []).length ? state.files_touched.slice(0, 10).map((file: string) => <div className="mini-note mono" key={file}>{file}</div>) : <div className="muted">none</div>}</div>
      </div>
    </div>
  )
}

function FindingsView() {
  const [items, setItems] = useState<any[]>([])
  useEffect(() => {
    fetch(`${API}/api/findings`).then(r => r.json()).then(data => setItems(data.items || []))
  }, [])
  return (
    <div className="card">
      <div className="card-title">Findings notebook</div>
      {items.length === 0 ? (
        <div className="muted">No findings yet.</div>
      ) : (
        <div className="stream-list">
          {items.map(item => (
            <div className={`stream-item tier-${item.tier}`} key={`${item.turn}-${item.hash}`}>
              <div className="row space">
                <strong>{item.finding?.type || 'finding'}</strong>
                <span className="muted">{item.finding?.severity} &middot; {item.finding?.target}</span>
              </div>
              <div style={{ marginTop: 8 }}>{item.assistant}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
