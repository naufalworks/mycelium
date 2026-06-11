import React, { useEffect, useMemo, useRef, useState } from 'react'

type View = 'dashboard' | 'stream' | 'graph' | 'vault' | 'findings'

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
type BackupList = { backup_root: string; items: Array<any> }
type ActionResult = { ok: boolean; message?: string; data?: any }
type DangerousAction = 'restore' | 'migrate' | null
type GraphData = { ok: boolean; nodes: Array<any>; links: Array<any>; sessions_considered: number; entities_considered: number }

const API = ''

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function ellipse(text: string, n = 180) {
  if (!text) return ''
  return text.length > n ? `${text.slice(0, n)}…` : text
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
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
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
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [sessionDetail, setSessionDetail] = useState<any>(null)
  const [selectedBackupPath, setSelectedBackupPath] = useState('')
  const [targetRoot, setTargetRoot] = useState('')
  const [actionResult, setActionResult] = useState<ActionResult | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmAction, setConfirmAction] = useState<DangerousAction>(null)
  const [confirmText, setConfirmText] = useState('')
  const confirmInputRef = useRef<HTMLInputElement | null>(null)

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

  useEffect(() => {
    if (!confirmAction) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setConfirmAction(null)
        setConfirmText('')
      }
      if (event.key === 'Enter' && confirmReady && busy === null) {
        void executeConfirmed()
      }
    }
    const timer = window.setTimeout(() => confirmInputRef.current?.focus(), 20)
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [confirmAction, busy])

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
  const positions = useMemo(() => graphPositions(graph?.nodes || []), [graph])
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
            {(['dashboard', 'stream', 'graph', 'vault', 'findings'] as View[]).map(v => (
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
                        <div className="muted">{s.last_ts} · {s.last_type} · tier {s.last_tier}</div>
                        <div className="chips">{(s.entities || []).map(ent => <span className="chip" key={ent}>{ent}</span>)}</div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="card">
                  <div className="card-title">Integrity / daemon</div>
                  <div className="detail-list">
                    <div><strong>Last turn:</strong> <span className="muted">{status?.last_turn?.session} · {status?.last_turn?.ts}</span></div>
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
                        <span className="muted">#{item.turn} · {item.type} · {item.ts}</span>
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
                        <div className="metric-sub">{sessionDetail.total} turn(s) · {sessionDetail.first_ts} → {sessionDetail.last_ts}</div>
                      </div>
                      <div className="chips">
                        {Object.entries(sessionDetail.tiers || {}).map(([tier, count]) => <span className="chip" key={tier}>tier {tier} · {String(count)}</span>)}
                      </div>
                    </div>
                    <div className="grid cols-2">
                      <div className="result-panel">
                        <div className="result-label">Top entities</div>
                        <div className="chips">{(sessionDetail.entities || []).slice(0, 8).map((ent: any) => <span className="chip" key={ent.name}>{ent.name} · {ent.count}</span>)}</div>
                      </div>
                      <div className="result-panel">
                        <div className="result-label">Types</div>
                        <div className="chips">{Object.entries(sessionDetail.types || {}).map(([kind, count]) => <span className="chip" key={kind}>{kind} · {String(count)}</span>)}</div>
                      </div>
                    </div>
                    <div className="grid cols-3">
                      <div className="result-panel">
                        <div className="result-label">Decisions</div>
                        <div className="detail-list">{sessionMeta.decisions.length ? sessionMeta.decisions.map((item: any) => <div key={item.hash} className="mini-note">{ellipse(item.assistant || item.user || '', 120)}</div>) : <div className="muted">none</div>}</div>
                      </div>
                      <div className="result-panel">
                        <div className="result-label">Findings</div>
                        <div className="detail-list">{sessionMeta.findings.length ? sessionMeta.findings.map((item: any) => <div key={item.hash} className="mini-note">{item.finding?.type || item.type} · {item.finding?.severity || item.tier}</div>) : <div className="muted">none</div>}</div>
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
                            <span className="muted">#{item.turn} · {item.ts}</span>
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

          {view === 'graph' && (
            <div className="grid cols-2">
              <div className="card graph-card">
                <div className="row space">
                  <div>
                    <div className="card-title">Branch / connections</div>
                    <div className="muted">session ↔ entity continuity map</div>
                  </div>
                  <div className="chips">
                    <span className="chip">sessions {graph?.sessions_considered ?? 0}</span>
                    <span className="chip">entities {graph?.entities_considered ?? 0}</span>
                  </div>
                </div>
                <svg viewBox="0 0 840 500" className="graph-svg">
                  {graph?.links?.map((link, idx) => {
                    const a = positions[link.source]
                    const b = positions[link.target]
                    if (!a || !b) return null
                    return <line key={idx} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`graph-link ${link.kind}`} strokeWidth={Math.min(5, 1 + link.weight * 0.7)} />
                  })}
                  {graph?.nodes?.map((node) => {
                    const p = positions[node.id]
                    if (!p) return null
                    const r = node.kind === 'session' ? 24 : 14 + Math.min(10, node.weight)
                    return (
                      <g key={node.id} transform={`translate(${p.x}, ${p.y})`}>
                        <title>{node.label}</title>
                        <circle r={r} className={`graph-node ${node.kind}`} onClick={() => node.kind === 'session' && openSession(node.label)} />
                        <text y={r + 18} textAnchor="middle" className="graph-label">{shortLabel(node.label)}</text>
                      </g>
                    )
                  })}
                </svg>
              </div>
              <div className="card">
                <div className="card-title">Connection notes</div>
                <div className="detail-list">
                  <div className="warning-box">
                    <strong>How to read</strong>
                    <div className="muted">large circles = denser nodes. inner ring = sessions. outer ring = recurring entities.</div>
                  </div>
                  <div className="result-panel">
                    <div className="result-label">Top session nodes</div>
                    <div className="detail-list">
                      {(graph?.nodes || []).filter(n => n.kind === 'session').slice(0, 6).map((node: any) => (
                        <div key={node.id} className="row space"><span>{node.label}</span><span className="muted">weight {node.weight}</span></div>
                      ))}
                    </div>
                  </div>
                  <div className="result-panel">
                    <div className="result-label">Recurring entities</div>
                    <div className="chips">{(graph?.nodes || []).filter(n => n.kind === 'entity').slice(0, 12).map((node: any) => <span className="chip" key={node.id}>{node.label}</span>)}</div>
                  </div>
                  <div className="muted">tip: click a session node to open its inspector.</div>
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
                    <button className="action-btn" onClick={createBackup}>Backup now</button>
                    <button className="action-btn secondary" onClick={exportLatest}>Export latest</button>
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
                      <div className="muted">verify backup → dry-run import → restore only after review</div>
                    </div>
                  </div>
                  <div className="row" style={{ marginTop: 18 }}>
                    <button className="action-btn secondary" disabled={busy !== null} onClick={() => runVaultAction('verify-backup', '/api/backups/verify', { path: selectedBackupPath })}>Verify backup</button>
                    <button className="action-btn secondary" disabled={busy !== null} onClick={() => runVaultAction('import-dry', '/api/import/dry-run', { path: selectedBackupPath, target_root: targetRoot })}>Dry-run import</button>
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
                      <div className="muted">safety snapshot first → copy runtime → relink source surface</div>
                    </div>
                  </div>
                  <div className="row" style={{ marginTop: 18 }}>
                    <button className="action-btn secondary" disabled={busy !== null} onClick={() => runVaultAction('migrate-dry', '/api/migrate/dry-run', { target_root: targetRoot })}>Dry-run migrate</button>
                    <button className="action-btn danger" disabled={busy !== null} onClick={() => askConfirm('migrate')}>Migrate now</button>
                  </div>
                </div>
              </div>

              <div className="card">
                <div className="card-title">Vault action result</div>
                {busy ? (
                  <div className="muted">running {busy}…</div>
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
          <div className="modal-shell danger-card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="confirm-title">
            <div className="card-title">Confirmation required</div>
            <div id="confirm-title" className="confirm-head">{confirmAction === 'restore' ? 'Confirm restore' : 'Confirm migrate'}</div>
            <div className="muted">{confirmAction === 'restore' ? 'This will overwrite target content with the selected backup.' : 'This will create a safety snapshot, copy runtime data, then rewrite runtime links.'}</div>
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
            <div className="warning-box">
              <strong>Type to continue</strong>
              <div className="muted">Type <span className="mono">{confirmLabel}</span>. Esc or click outside cancels.</div>
            </div>
            <input
              ref={confirmInputRef}
              className="search mono"
              value={confirmText}
              onChange={e => setConfirmText(e.target.value)}
              placeholder={`type ${confirmLabel}`}
            />
            <div className="row space">
              <button className="action-btn secondary" onClick={closeConfirm}>Cancel</button>
              <button className="action-btn danger" disabled={!confirmReady || busy !== null} onClick={executeConfirmed}>
                {confirmAction === 'restore' ? 'Confirm restore' : 'Confirm migrate'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
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
                <span className="muted">{item.finding?.severity} · {item.finding?.target}</span>
              </div>
              <div style={{ marginTop: 8 }}>{item.assistant}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
