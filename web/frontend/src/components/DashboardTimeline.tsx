import { useEffect, useMemo, useRef, useState } from 'react'
import './DashboardTimeline.css'

// ── Types (mirror App.tsx) ─────────────────────────────
export interface StreamItem {
  turn: number; tier: string; type: string; session: string
  ts: string; user: string; assistant: string
  entities: string[]; hash: string; prev_hash: string
}

// ── Config ─────────────────────────────────────────────
const WINDOW_MS = 90_000              // 90-second pulse window
const FADE_AFTER_MS = 60_000          // ticks older than this dim out
const REMOVE_AFTER_MS = 95_000        // ticks older than this drop off
const LANE_HEIGHT = 44
const LANE_LABEL_WIDTH = 168
const LANE_PADDING = 16

// Tier color mapping — drawn from the existing mycelium tokens.
const TIER_COLOR: Record<string, string> = {
  core:      'var(--accent-teal)',     // the brain's living memory
  ephemeral: 'var(--accent-amber)',    // working scratch
  archived:  '#818CF8',                // quiet, lavender — needs new token
}

// Entry-type sizing — important turns read louder.
const TYPE_SCALE: Record<string, number> = {
  conversation: 6,
  fact:         9,
  finding:      12,
  system:       4,
}

const TIER_RANK: Record<string, number> = {
  core: 0, ephemeral: 1, archived: 2, '': 3,
}

function tierColor(tier: string): string {
  return TIER_COLOR[tier.toLowerCase()] ?? 'var(--text-tertiary)'
}

function tickSize(type: string): number {
  return TYPE_SCALE[type.toLowerCase()] ?? 6
}

function parseTs(ts: string): number {
  // tolerate ISO with or without Z
  const t = ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z'
  const ms = Date.parse(t)
  return Number.isNaN(ms) ? Date.now() : ms
}

function shortSession(s: string): string {
  if (!s) return '—'
  // take last 14 chars of the session id, with a leading ellipsis if truncated
  return s.length > 14 ? '…' + s.slice(-13) : s
}

function timeAgo(ms: number, now: number): string {
  const delta = Math.max(0, now - ms)
  if (delta < 1_000) return 'now'
  if (delta < 60_000) return `${Math.floor(delta / 1000)}s ago`
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`
  return `${Math.floor(delta / 3_600_000)}h ago`
}

// ── Component ──────────────────────────────────────────
interface Props {
  stream: StreamItem[]
  status: any | null
  daemon: any | null
}

export default function DashboardTimeline({ stream, status, daemon }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [now, setNow] = useState(Date.now())
  const [selected, setSelected] = useState<StreamItem | null>(null)
  const [width, setWidth] = useState(960)
  const knownIds = useRef<Set<number>>(new Set())

  // 1Hz tick — drives the animation of items drifting left.
  useEffect(() => {
    const iv = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(iv)
  }, [])

  // Resize observer — keep the timeline filling the viewport.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      for (const e of entries) setWidth(e.contentRect.width)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ── Derive session lanes + filtered stream ────────────
  const { lanes, visible } = useMemo(() => {
    const liveNow = Date.now()
    const live = stream.filter(s => liveNow - parseTs(s.ts) < REMOVE_AFTER_MS)

    // Session order: most recent activity first.
    const sessionOrder: string[] = []
    const seen = new Set<string>()
    for (const item of live) {
      if (!seen.has(item.session)) {
        seen.add(item.session)
        sessionOrder.push(item.session)
      }
    }

    return { lanes: sessionOrder, visible: live }
  }, [stream])

  // ── Stats — kept tiny: only what the timeline can't show ──
  const tickRate = useMemo(() => {
    const recent = visible.filter(s => now - parseTs(s.ts) < 60_000).length
    return recent
  }, [visible, now])

  const alive = !!daemon?.last_assistant_id || !!status
  const totalTurns = status?.total_turns ?? 0
  const totalSessions = status?.total_sessions ?? lanes.length

  // Plot area width
  const plotWidth = Math.max(0, width - LANE_LABEL_WIDTH - LANE_PADDING * 2)

  // Mark new arrivals — used by the CSS animation to slide in from the right.
  useEffect(() => {
    const fresh: number[] = []
    for (const s of stream) {
      if (!knownIds.current.has(s.turn)) {
        fresh.push(s.turn)
        knownIds.current.add(s.turn)
      }
    }
    // We don't need to store fresh — CSS handles it via animation re-mount when stream updates.
    // But we want a re-render of those ticks as "just arrived". Force a re-render by setting now
    // once on stream change.
    if (fresh.length) setNow(Date.now())
  }, [stream])

  return (
    <div className="dt-root">
      {/* ── Header strip — kept deliberately quiet ──────── */}
      <header className="dt-header">
        <div className="dt-brand">
          <span className="dt-mark">◉</span>
          <span className="dt-name">mycelium</span>
          <span className="dt-sub">live brain</span>
        </div>
        <div className="dt-meta">
          <div className="dt-meta-item">
            <span className="dt-meta-num">{totalTurns.toLocaleString()}</span>
            <span className="dt-meta-lbl">turns</span>
          </div>
          <div className="dt-meta-item">
            <span className="dt-meta-num">{totalSessions}</span>
            <span className="dt-meta-lbl">sessions</span>
          </div>
          <div className="dt-meta-item">
            <span className="dt-meta-num">{tickRate}</span>
            <span className="dt-meta-lbl">/ min</span>
          </div>
          <div className="dt-live" data-on={alive ? 'true' : 'false'}>
            <span className="dt-live-dot" />
            <span className="dt-live-lbl">{alive ? 'alive' : 'silent'}</span>
          </div>
        </div>
      </header>

      {/* ── Timeline canvas ──────────────────────────────── */}
      <div className="dt-canvas" ref={containerRef}>
        {/* Time axis — five ticks across the window */}
        <div className="dt-axis">
          <div className="dt-axis-label dt-axis-left">
            <span>−{Math.round(WINDOW_MS / 1000)}s</span>
          </div>
          {[-0.75, -0.5, -0.25, 0].map(frac => (
            <div
              key={frac}
              className="dt-axis-tick"
              style={{ left: `${LANE_LABEL_WIDTH + LANE_PADDING + (1 + frac) * plotWidth}px` }}
            />
          ))}
          <div className="dt-axis-label dt-axis-right">
            <span>now</span>
          </div>
        </div>

        {/* Session lanes */}
        <div className="dt-lanes">
          {lanes.length === 0 && (
            <div className="dt-empty">
              <div className="dt-empty-mark">○</div>
              <div className="dt-empty-text">The brain is quiet.</div>
              <div className="dt-empty-sub">Waiting for the first turn to land…</div>
            </div>
          )}

          {lanes.map((session, laneIdx) => {
            const items = visible
              .filter(s => s.session === session)
              .sort((a, b) => parseTs(b.ts) - parseTs(a.ts))

            const lastTs = items[0] ? parseTs(items[0].ts) : 0
            const laneAlive = now - lastTs < 15_000

            return (
              <div
                key={session}
                className="dt-lane"
                data-active={laneAlive ? 'true' : 'false'}
                style={{ height: LANE_HEIGHT }}
              >
                {/* Session label — fixed-width gutter on the left */}
                <div className="dt-lane-label">
                  <div className="dt-lane-id">{shortSession(session)}</div>
                  <div className="dt-lane-meta">
                    {items.length} turn{items.length === 1 ? '' : 's'}
                    {laneAlive && <span className="dt-lane-pulse" />}
                  </div>
                </div>

                {/* The lane itself — the plot area */}
                <div className="dt-lane-plot" style={{ width: plotWidth }}>
                  {/* Lane baseline */}
                  <div className="dt-lane-baseline" />

                  {/* Tier dots */}
                  {items.map((item, i) => {
                    const ts = parseTs(item.ts)
                    const age = now - ts
                    if (age > REMOVE_AFTER_MS) return null

                    const x = plotWidth * (1 - age / WINDOW_MS)
                    const size = tickSize(item.type)
                    const color = tierColor(item.tier)
                    const faded = age > FADE_AFTER_MS ? 0.35 : 1
                    const isNew = i === 0 && age < 5000  // most-recent item, just arrived

                    return (
                      <button
                        key={item.turn}
                        className="dt-tick"
                        data-new={isNew ? 'true' : 'false'}
                        style={{
                          left: x,
                          width: size,
                          height: size,
                          background: color,
                          opacity: faded,
                          boxShadow: `0 0 12px ${color}`,
                        }}
                        onClick={() => setSelected(item)}
                        title={`turn #${item.turn} · ${item.tier || 'untiered'} · ${item.type}`}
                        aria-label={`Turn ${item.turn}, ${item.tier}, ${item.type}`}
                      />
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>

        {/* ── Detail panel — slides in from the right when a tick is selected ── */}
        {selected && (
          <aside className="dt-detail">
            <button className="dt-detail-close" onClick={() => setSelected(null)} aria-label="Close">
              ✕
            </button>
            <div className="dt-detail-head">
              <span
                className="dt-detail-tier"
                style={{ background: tierColor(selected.tier) }}
              />
              <span className="dt-detail-turn">turn #{selected.turn}</span>
              <span className="dt-detail-time">{timeAgo(parseTs(selected.ts), now)}</span>
            </div>
            <div className="dt-detail-meta">
              <span>tier <b>{selected.tier || '—'}</b></span>
              <span>type <b>{selected.type || '—'}</b></span>
              <span>session <b>{shortSession(selected.session)}</b></span>
            </div>
            <div className="dt-detail-body">
              <div className="dt-detail-msg dt-detail-msg--user">
                <div className="dt-detail-who">user</div>
                <div className="dt-detail-text">{selected.user || '(empty)'}</div>
              </div>
              <div className="dt-detail-msg dt-detail-msg--asst">
                <div className="dt-detail-who">assistant</div>
                <div className="dt-detail-text">{selected.assistant || '(empty)'}</div>
              </div>
            </div>
            {selected.entities?.length > 0 && (
              <div className="dt-detail-entities">
                {selected.entities.map(e => (
                  <span key={e} className="dt-entity">{e}</span>
                ))}
              </div>
            )}
            <div className="dt-detail-hash" title={selected.hash}>
              <span className="dt-detail-hash-lbl">hash</span>
              <span className="dt-detail-hash-val">
                {selected.hash?.slice(0, 10) || '—'}…
              </span>
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}
