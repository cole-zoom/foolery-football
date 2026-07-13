import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, LayoutDashboard, Loader2, RotateCcw, Swords, TrendingUp } from 'lucide-react'
import { api, MODELS, type Candidate, type Model, type Pool, type SlotDecision } from '@/lib/api'
import { ComparisonView } from '@/components/ComparisonView'
import { EntryForm } from '@/components/EntryForm'
import { FieldSelect } from '@/components/FieldSelect'
import { RiskKnob } from '@/components/RiskKnob'
import { SlotCard } from '@/components/SlotCard'
import { RecommendationPanel } from '@/components/RecommendationPanel'
import { PlayerDrawer } from '@/components/PlayerDrawer'
import { WeekPicker, SeasonPicker } from '@/components/WeekPicker'
import { ThemeToggle } from '@/components/ThemeToggle'
import { TeamPrefs } from '@/components/TeamPrefs'
import { useDebouncedValue } from '@/lib/useDebouncedValue'
import { cn } from '@/lib/cn'

type Session = {
  username: string
  leagueId: string
  season: number
}

type PinnedPick = { player: Candidate['player']; score: number }
type View = 'lineup' | 'comparison'

const REGULAR_SEASON_LAST_WEEK = 18
const RISK_DEBOUNCE_MS = 400

export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null)
  const [openPlayerId, setOpenPlayerId] = useState<string | null>(null)
  const [risk, setRisk] = useState(0.5)
  const [pool, setPool] = useState<Pool>('roster')
  const [model, setModel] = useState<Model>('context')
  const [week, setWeek] = useState<number | null>(null)
  const [prefer, setPrefer] = useState<string | null>(null)
  const [avoid, setAvoid] = useState<string | null>(null)
  const [pins, setPins] = useState<Record<string, PinnedPick>>({})
  const [view, setView] = useState<View>('lineup')

  if (!session) {
    return <EntryForm onSubmit={(s) => { setSession(s); setWeek(null); setPins({}) }} />
  }

  return (
    <SessionView
      session={session}
      onReset={() => {
        setSession(null)
        setActiveSlotId(null)
        setOpenPlayerId(null)
        setWeek(null)
        setPrefer(null)
        setAvoid(null)
        setPins({})
        setView('lineup')
      }}
      activeSlotId={activeSlotId}
      setActiveSlotId={setActiveSlotId}
      openPlayerId={openPlayerId}
      setOpenPlayerId={setOpenPlayerId}
      risk={risk}
      setRisk={setRisk}
      pool={pool}
      setPool={setPool}
      model={model}
      setModel={setModel}
      week={week}
      setWeek={setWeek}
      prefer={prefer}
      avoid={avoid}
      setPrefs={(p) => { setPrefer(p.prefer); setAvoid(p.avoid) }}
      onSessionChange={setSession}
      pins={pins}
      setPins={setPins}
      view={view}
      setView={setView}
    />
  )
}

function SessionView({
  session,
  onReset,
  activeSlotId,
  setActiveSlotId,
  openPlayerId,
  setOpenPlayerId,
  risk,
  setRisk,
  pool,
  setPool,
  model,
  setModel,
  week,
  setWeek,
  prefer,
  avoid,
  setPrefs,
  onSessionChange,
  pins,
  setPins,
  view,
  setView,
}: {
  session: Session
  onReset: () => void
  activeSlotId: string | null
  setActiveSlotId: (s: string | null) => void
  openPlayerId: string | null
  setOpenPlayerId: (s: string | null) => void
  risk: number
  setRisk: (n: number) => void
  pool: Pool
  setPool: (p: Pool) => void
  model: Model
  setModel: (m: Model) => void
  week: number | null
  setWeek: (w: number | null) => void
  prefer: string | null
  avoid: string | null
  setPrefs: (p: { prefer: string | null; avoid: string | null }) => void
  onSessionChange: (s: Session) => void
  pins: Record<string, PinnedPick>
  setPins: React.Dispatch<React.SetStateAction<Record<string, PinnedPick>>>
  view: View
  setView: (v: View) => void
}) {
  // Debounce the slider so the API only fires when the user pauses.
  const debouncedRisk = useDebouncedValue(risk, RISK_DEBOUNCE_MS)
  const riskPending = debouncedRisk !== risk
  const queryClient = useQueryClient()

  const ctxQ = useQuery({
    queryKey: ['context', session.leagueId, session.username, session.season],
    queryFn: () => api.context(session.leagueId, session.username, session.season),
  })

  const stateQ = useQuery({ queryKey: ['state'], queryFn: api.state })

  // Sleeper assigns a fresh league_id every season. When the user picks
  // a different season we have to resolve the matching league_id for
  // that season — same league name, different id — before any query
  // can use it.
  const switchSeason = useMutation({
    mutationFn: async (newSeason: number) => {
      const out = await api.userLeagues(session.username, newSeason)
      if (out.leagues.length === 0) {
        throw new Error(
          `${session.username} has no leagues in ${newSeason}.`,
        )
      }
      const currentName = ctxQ.data?.league.name?.trim().toLowerCase()
      const byName = currentName
        ? out.leagues.find((l) => l.name.trim().toLowerCase() === currentName)
        : null
      return { newSeason, leagueId: (byName ?? out.leagues[0]).league_id }
    },
    onSuccess: ({ newSeason, leagueId }) => {
      onSessionChange({ ...session, season: newSeason, leagueId })
      setWeek(null)
      setPins({})
    },
  })

  useEffect(() => {
    if (week !== null || !stateQ.data) return
    const live = stateQ.data
    if (session.season < live.season) {
      setWeek(REGULAR_SEASON_LAST_WEEK)
    } else {
      setWeek(Math.max(1, Math.min(REGULAR_SEASON_LAST_WEEK, live.week - 1)))
    }
  }, [stateQ.data, session.season, week, setWeek])

  const decisionsQ = useQuery({
    enabled: !!ctxQ.data && week !== null,
    queryKey: [
      'decisions',
      session.leagueId,
      session.username,
      session.season,
      week,
      debouncedRisk,
      pool,
      model,
      prefer,
      avoid,
    ],
    queryFn: () =>
      api.decisions({
        league_id: session.leagueId,
        user: session.username,
        risk: debouncedRisk,
        pool,
        model,
        season: session.season,
        week: week ?? undefined,
        prefer_team: prefer ?? undefined,
        avoid_team: avoid ?? undefined,
      }),
    placeholderData: (prev) => prev,
  })

  // Once the current week resolves, prefetch the other playable weeks so
  // flipping the WeekPicker is instant. "Playable" means strictly: weeks
  // that could have actually occurred. For past seasons that's 1..18; for
  // the live season it's 1..live.week (anything beyond hasn't happened,
  // so scoring just falls back to boilerplate).
  //
  // Deliberately NOT a parallel fan-out: 17 concurrent /decisions calls
  // saturate the API's single-vCPU instance, and a week the user then
  // *clicks* queues behind all of them. A small worker pool (nearest
  // weeks first — the ones the picker reaches soonest) keeps the server
  // free to answer interactive requests immediately. Each prefetch
  // retries twice with backoff for transient Sleeper hiccups.
  useEffect(() => {
    if (!decisionsQ.data || !stateQ.data || riskPending || week === null) return

    const live = stateQ.data
    const maxWeek =
      session.season < live.season
        ? REGULAR_SEASON_LAST_WEEK
        : Math.min(REGULAR_SEASON_LAST_WEEK, Math.max(1, live.week))

    const queue: number[] = []
    for (let w = 1; w <= maxWeek; w++) {
      if (w !== week) queue.push(w)
    }
    queue.sort((a, b) => Math.abs(a - week) - Math.abs(b - week))

    // Cancellation is soft: in-flight prefetches finish (their result is
    // still cacheable), but no new ones launch after a knob change
    // re-runs this effect.
    let cancelled = false
    const PREFETCH_CONCURRENCY = 2
    const worker = async () => {
      while (!cancelled) {
        const w = queue.shift()
        if (w === undefined) return
        await queryClient.prefetchQuery({
          queryKey: [
            'decisions',
            session.leagueId,
            session.username,
            session.season,
            w,
            debouncedRisk,
            pool,
            model,
            prefer,
            avoid,
          ],
          queryFn: () =>
            api.decisions({
              league_id: session.leagueId,
              user: session.username,
              risk: debouncedRisk,
              pool,
              model,
              season: session.season,
              week: w,
              prefer_team: prefer ?? undefined,
              avoid_team: avoid ?? undefined,
            }),
          // Treat cached results as fresh for 5 min so re-renders of this
          // effect don't refire identical requests.
          staleTime: 5 * 60 * 1000,
          retry: 2,
          retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
        })
      }
    }
    for (let i = 0; i < PREFETCH_CONCURRENCY; i++) void worker()

    return () => {
      cancelled = true
    }
  }, [
    decisionsQ.data,
    stateQ.data,
    riskPending,
    week,
    debouncedRisk,
    pool,
    model,
    prefer,
    avoid,
    session.leagueId,
    session.username,
    session.season,
    queryClient,
  ])

  const ctx = ctxQ.data
  const decisionsByPos = useMemo(() => {
    const m = new Map<string, SlotDecision>()
    decisionsQ.data?.decisions.forEach((d) => m.set(d.slot_id, d))
    return m
  }, [decisionsQ.data])

  // Show a "caching snapshot" banner the first time we load a given
  // (league, season) — that's when the backend may need ~15-30s to
  // download the season's snapshot from Sleeper.
  const seenKey = `${session.leagueId}:${session.season}`
  const seenSnapshotsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (decisionsQ.data) seenSnapshotsRef.current.add(seenKey)
  }, [decisionsQ.data, seenKey])
  const cachingSnapshot =
    (decisionsQ.isFetching || ctxQ.isFetching) &&
    !seenSnapshotsRef.current.has(seenKey)

  const seasonOptions = useMemo(() => {
    if (!stateQ.data) return [session.season]
    const top = Math.max(stateQ.data.season, session.season)
    const arr: number[] = []
    for (let s = top; s >= top - 7; s--) arr.push(s)
    return arr
  }, [stateQ.data, session.season])

  // Total projection = pinned picks + model recommendations for unpinned slots.
  const effectiveTotal = useMemo(() => {
    if (!decisionsQ.data) return null
    let sum = 0
    let varianceSum = 0
    let allPresent = true
    // How many projected points the model's lineup gains over the
    // user's current Sleeper lineup — the number that says whether
    // following the recommendations changes the outcome.
    let edge = 0
    for (const d of decisionsQ.data.decisions) {
      const pin = pins[d.slot_id]
      if (pin) {
        sum += pin.score
        // Don't have variance for pinned (it's a final_score). Assume
        // the model's variance for this slot as an approximation.
        if (d.recommended) varianceSum += d.recommended.score.projected_variance ** 2
      } else if (d.recommended) {
        sum += d.recommended.score.projected_mean
        varianceSum += d.recommended.score.projected_variance ** 2
        const starterScore = d.current_starter_score ?? null
        if (!d.matches_current && starterScore) {
          edge += d.recommended.score.projected_mean - starterScore.projected_mean
        }
      } else {
        allPresent = false
      }
    }
    return {
      total: sum,
      stddev: Math.sqrt(varianceSum),
      complete: allPresent,
      edge,
    }
  }, [decisionsQ.data, pins])

  const pinnedCount = Object.keys(pins).length

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="sticky top-0 z-30 backdrop-blur-md bg-[var(--color-ink-base)]/85 border-b hairline">
        <div className="max-w-[1400px] mx-auto px-8 py-3.5 flex items-center justify-between gap-6">
          <div className="flex items-center gap-3 shrink-0">
            <div className="w-8 h-8 rounded-md bg-ink-3 border hairline grid place-items-center">
              <LayoutDashboard size={14} className="text-[var(--color-signal)]" />
            </div>
            <div className="flex flex-col leading-tight">
              <span className="stamp text-[10px] text-ink-7">FOOTBALL GENIE</span>
              <span className="text-sm text-ink-12 font-medium truncate max-w-[220px]">
                {ctx?.league.name ?? '…'}
              </span>
            </div>
            <div className="flex items-center rounded-md border hairline bg-ink-2 p-0.5 ml-3">
              <ViewTab
                active={view === 'lineup'}
                onClick={() => setView('lineup')}
                icon={<LayoutDashboard size={11} />}
                label="LINEUP"
              />
              <ViewTab
                active={view === 'comparison'}
                onClick={() => setView('comparison')}
                icon={<Swords size={11} />}
                label="MODEL VS YOU"
              />
            </div>
          </div>

          <div className="flex items-center gap-4">
            <SeasonPicker
              value={session.season}
              onChange={(s) => {
                if (s === session.season) return
                switchSeason.mutate(s)
              }}
              options={seasonOptions}
            />
            <WeekPicker
              value={week ?? 1}
              onChange={(w) => {
                setWeek(w)
                setPins({})
              }}
            />
            {view === 'lineup' && (
              <FieldSelect
                label="POOL"
                value={pool}
                onChange={(v) => {
                  setPool(v as Pool)
                  setPins({})
                }}
                options={[
                  { value: 'roster', label: 'My roster' },
                  { value: 'both', label: 'Roster + waivers' },
                  { value: 'waivers', label: 'Waivers only' },
                ]}
                size="sm"
                triggerClassName="min-w-[150px]"
              />
            )}
            <FieldSelect
              label="MODEL"
              value={model}
              onChange={(v) => {
                setModel(v as Model)
                setPins({})
              }}
              options={MODELS.map((m) => ({ value: m.value, label: m.label }))}
              size="sm"
              triggerClassName="min-w-[170px]"
            />
            <Divider />
            <RiskKnob value={risk} onChange={setRisk} pending={riskPending} />
            <Divider />
            <div className="flex flex-col gap-1.5">
              <span className="stamp text-[10px] text-ink-7 leading-none">CONTROLS</span>
              <div className="flex items-center gap-1.5">
                {view === 'lineup' && (
                  <TeamPrefs
                    prefer={prefer}
                    avoid={avoid}
                    onChange={setPrefs}
                  />
                )}
                <ThemeToggle />
                <button
                  onClick={onReset}
                  aria-label="Switch session"
                  className="h-8 px-2.5 rounded-md bg-ink-2 hover:bg-ink-3 border hairline stamp text-[10px] text-ink-9 hover:text-ink-12 flex items-center gap-1.5 transition cursor-pointer"
                >
                  <RotateCcw size={11} />
                  SWITCH
                </button>
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* Main */}
      {view === 'comparison' ? (
        <ComparisonView
          user={session.username}
          leagueId={session.leagueId}
          season={session.season}
          week={week}
          model={model}
          risk={debouncedRisk}
          onViewPlayer={setOpenPlayerId}
        />
      ) : (
      <main className="flex-1 max-w-[1400px] mx-auto w-full px-8 py-10 grid grid-cols-1 lg:grid-cols-[1fr_440px] gap-10">
        <section>
          <div className="mb-7 flex items-end justify-between gap-6">
            <div>
              <div className="stamp text-[10px] text-ink-7 mb-2">YOUR OPTIMAL LINEUP</div>
              <h2 className="display text-[36px] text-ink-12">
                Week <span className="text-[var(--color-signal)]">{week ?? '—'}</span> · {session.season}
              </h2>
              <p className="text-ink-8 text-sm mt-2 max-w-md leading-relaxed">
                Top recommendation per slot from the{' '}
                <span className="text-ink-11">{model}</span> model at{' '}
                <span className="text-ink-11">risk {debouncedRisk.toFixed(2)}</span>.
                Click a slot for the full ranked list.
                {pinnedCount > 0 && (
                  <>
                    {' '}<span className="text-[var(--color-good)]">{pinnedCount} pinned.</span>
                  </>
                )}
              </p>
            </div>

            <ProjectionCard
              total={effectiveTotal?.total ?? null}
              stddev={effectiveTotal?.stddev ?? null}
              edge={effectiveTotal?.edge ?? null}
              loading={decisionsQ.isFetching || riskPending}
            />
          </div>

          {switchSeason.isPending && <SwitchSeasonBanner />}
          {switchSeason.error && (
            <ErrorBanner message={(switchSeason.error as Error).message} />
          )}
          {cachingSnapshot && (
            <SnapshotCachingBanner season={session.season} />
          )}
          {decisionsQ.data?.using_prior_season && decisionsQ.data.prior_season && (
            <PriorSeasonBanner
              shownSeason={session.season}
              priorSeason={decisionsQ.data.prior_season}
              week={week}
            />
          )}

          {(ctxQ.isLoading || (!ctx && !ctxQ.error)) && (
            <div className="space-y-2">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="h-[68px] rounded-md bg-ink-2 border hairline animate-pulse" />
              ))}
            </div>
          )}

          {ctxQ.error && <ErrorBanner message={(ctxQ.error as Error).message} />}

          {ctx && (
            <div className="space-y-2">
              {ctx.slots.filter((s) => s.selectable).map((s, i) => {
                const d = decisionsByPos.get(s.slot_id)
                const pin = pins[s.slot_id] ?? null
                return (
                  <SlotCard
                    key={s.slot_id}
                    index={i}
                    active={s.slot_id === activeSlotId}
                    onClick={() => setActiveSlotId(s.slot_id)}
                    pinned={pin}
                    onClearPin={pin ? () => {
                      setPins((prev) => {
                        const { [s.slot_id]: _drop, ...rest } = prev
                        return rest
                      })
                    } : undefined}
                    decision={{
                      slot_id: s.slot_id,
                      slot: s.slot,
                      recommended_player: d?.recommended?.player ?? null,
                      current_player: d?.current_starter ?? s.starter_player,
                      matches: d?.matches_current ?? true,
                      score: d?.recommended?.score.final_score ?? null,
                      variance: d?.recommended?.score.projected_variance ?? null,
                      confidence: d?.recommended?.score.confidence ?? null,
                      swapGain:
                        d && !d.matches_current && d.recommended && d.current_starter_score
                          ? d.recommended.score.projected_mean -
                            d.current_starter_score.projected_mean
                          : null,
                    }}
                  />
                )
              })}
              {ctx.slots.filter((s) => !s.selectable).length > 0 && (
                <details className="mt-6">
                  <summary className="cursor-pointer stamp text-[10px] text-ink-7 hover:text-ink-11 px-2 py-1 select-none">
                    SHOW BENCH ({ctx.bench.length})
                  </summary>
                  <div className="mt-3 space-y-1.5">
                    {ctx.bench.map((p) => (
                      <div key={p.player_id} className="flex items-center gap-3 px-4 py-2 bg-ink-2/50 border hairline rounded-md">
                        <span className="stamp text-[10px] text-ink-7 w-10">BN</span>
                        <span className="text-[13px] text-ink-11 truncate flex-1">{p.full_name}</span>
                        <span className="stamp text-[10px] text-ink-8">{p.position}</span>
                        <span className="stamp text-[10px] text-ink-7 w-8 text-right">{p.team ?? '—'}</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          )}
        </section>

        <aside className="space-y-6">
          {ctx && (
            <div className="bg-ink-2 border hairline rounded-lg p-6 rise-in">
              <div className="stamp text-[10px] text-ink-7 mb-3">MANAGER</div>
              <div className="display text-2xl text-ink-12 mb-1">
                {ctx.display_name ?? ctx.username}
              </div>
              <div className="stamp text-[10px] text-ink-7">@{ctx.username}</div>

              <div className="grid grid-cols-3 gap-4 mt-6 pt-5 border-t hairline">
                <SidebarStat label="STARTERS" value={ctx.slots.filter((s) => s.selectable).length} />
                <SidebarStat label="BENCH" value={ctx.bench.length} />
                <SidebarStat label="TOTAL" value={ctx.all_roster_players.length} />
              </div>
            </div>
          )}

          <div className="bg-ink-2 border hairline rounded-lg p-6">
            <div className="stamp text-[10px] text-ink-7 mb-3">HOW IT WORKS</div>
            <ol className="space-y-3 text-sm text-ink-10 leading-relaxed">
              <li className="flex gap-3">
                <span className="stamp text-[10px] text-[var(--color-signal)] shrink-0 pt-0.5">01</span>
                <span>The lineup updates as you move the risk slider or switch the scoring model. Each slot shows the model's top pick; <span className="text-ink-12 font-medium">SWAP +N</span> is the projected points gained by benching your current starter for it.</span>
              </li>
              <li className="flex gap-3">
                <span className="stamp text-[10px] text-[var(--color-signal)] shrink-0 pt-0.5">02</span>
                <span>Click any slot to see the full ranked list. Hit <span className="text-ink-12 font-medium">USE THIS PLAYER</span> to pin one over the model's pick.</span>
              </li>
              <li className="flex gap-3">
                <span className="stamp text-[10px] text-[var(--color-signal)] shrink-0 pt-0.5">03</span>
                <span><span className="text-ink-12 font-medium">SWAP</span> means the model disagrees with your Sleeper starter. <span className="text-ink-12 font-medium">MATCH</span> means you're set. <span className="text-ink-12 font-medium">PINNED</span> means you've overridden the model.</span>
              </li>
            </ol>
          </div>

          {(prefer || avoid) && (
            <div className="bg-ink-2 border hairline rounded-lg p-6">
              <div className="stamp text-[10px] text-ink-7 mb-3">ACTIVE PREFERENCES</div>
              <div className="space-y-2 text-sm text-ink-10">
                {prefer && <div><span className="stamp text-[10px] text-[var(--color-good)] mr-2">+10%</span>{prefer}</div>}
                {avoid && <div><span className="stamp text-[10px] text-[var(--color-signal)] mr-2">−10%</span>{avoid}</div>}
              </div>
            </div>
          )}
        </aside>
      </main>
      )}

      <RecommendationPanel
        open={!!activeSlotId}
        onOpenChange={(open) => !open && setActiveSlotId(null)}
        user={session.username}
        leagueId={session.leagueId}
        season={session.season}
        week={week}
        slotId={activeSlotId}
        risk={debouncedRisk}
        pool={pool}
        model={model}
        onPoolChange={setPool}
        pinnedPlayerId={activeSlotId ? (pins[activeSlotId]?.player.player_id ?? null) : null}
        onPin={(c) => {
          if (!activeSlotId) return
          setPins((prev) => ({
            ...prev,
            [activeSlotId]: { player: c.player, score: c.score.final_score },
          }))
        }}
        onUnpin={() => {
          if (!activeSlotId) return
          setPins((prev) => {
            const { [activeSlotId]: _drop, ...rest } = prev
            return rest
          })
        }}
        onViewPlayer={setOpenPlayerId}
      />

      <PlayerDrawer
        playerId={openPlayerId}
        leagueId={session.leagueId}
        season={session.season}
        week={week}
        onClose={() => setOpenPlayerId(null)}
      />
    </div>
  )
}

function Divider() {
  return <div className="h-9 w-px bg-[var(--color-ink-5)] mt-5" />
}

function ViewTab({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'h-7 px-2.5 rounded-[5px] stamp text-[10px] flex items-center gap-1.5 transition whitespace-nowrap',
        active
          ? 'bg-ink-4 text-ink-12 shadow-[inset_0_0_0_1px_var(--color-ink-6)]'
          : 'text-ink-8 hover:text-ink-11',
      )}
    >
      {icon}
      {label}
    </button>
  )
}

function ProjectionCard({
  total,
  stddev,
  edge,
  loading,
}: {
  total: number | null
  stddev: number | null
  edge: number | null
  loading: boolean
}) {
  return (
    <div className={cn(
      'bg-ink-2 border hairline rounded-lg px-5 py-4 transition',
      loading && 'opacity-70',
    )}>
      <div className="flex items-center gap-2 mb-1">
        <TrendingUp size={11} className="text-[var(--color-signal)]" />
        <span className="stamp text-[10px] text-ink-7">PROJECTED WEEKLY TOTAL</span>
        {loading && <Loader2 size={10} className="animate-spin text-ink-8" />}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="nums display text-4xl text-ink-12 font-bold leading-none">
          {total !== null ? total.toFixed(1) : '—'}
        </span>
        <span className="stamp text-[10px] text-ink-7">PTS</span>
      </div>
      {stddev !== null && (
        <div className="mt-1.5 stamp text-[10px] text-ink-8">
          ± {stddev.toFixed(1)} STDDEV
        </div>
      )}
      {edge !== null && edge > 0.05 && (
        <div
          className="mt-1.5 stamp text-[10px]"
          style={{ color: 'color-mix(in oklch, var(--color-good) 85%, white)' }}
          title="Projected points gained by making the recommended swaps instead of keeping your current Sleeper starters"
        >
          +{edge.toFixed(1)} VS YOUR CURRENT LINEUP
        </div>
      )}
    </div>
  )
}

function SidebarStat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="nums text-2xl text-ink-12 font-bold leading-none">{value}</div>
      <div className="stamp text-[9px] text-ink-7 mt-1.5">{label}</div>
    </div>
  )
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-[var(--color-signal)]/40 bg-[var(--color-signal)]/8 px-4 py-3 mb-3 text-sm text-ink-11">
      <span className="stamp text-[10px] text-[var(--color-signal)] mr-2">ERROR</span>
      {message}
    </div>
  )
}

function SwitchSeasonBanner() {
  return (
    <div className="rounded-md border hairline bg-ink-2 px-4 py-3 mb-3 flex items-center gap-3 text-sm text-ink-11">
      <Loader2 size={14} className="animate-spin text-[var(--color-signal)]" />
      <span>
        <span className="stamp text-[10px] text-ink-7 mr-2">SWITCHING</span>
        Finding this league's id for the chosen season…
      </span>
    </div>
  )
}

function PriorSeasonBanner({
  shownSeason,
  priorSeason,
  week,
}: {
  shownSeason: number
  priorSeason: number
  week: number | null
}) {
  return (
    <div className="rounded-md border border-[var(--color-signal)]/40 bg-[var(--color-signal)]/8 px-4 py-3 mb-3 text-sm text-ink-11 flex items-start gap-3">
      <span className="stamp text-[10px] text-[var(--color-signal)] mt-0.5 shrink-0">
        WEEK {week ?? '—'}
      </span>
      <span>
        No <span className="text-ink-12">{shownSeason}</span> games have been played yet.
        Projections are based on the full{' '}
        <span className="text-ink-12">{priorSeason}</span> season.
      </span>
    </div>
  )
}

function SnapshotCachingBanner({ season }: { season: number }) {
  return (
    <div className="relative overflow-hidden rounded-md border hairline bg-ink-2 px-5 py-4 mb-3 flex items-center gap-4">
      <div className="grid place-items-center w-10 h-10 rounded-md bg-ink-3 border hairline shrink-0">
        <Database size={16} className="text-[var(--color-signal)]" />
      </div>
      <div className="flex-1">
        <div className="flex items-center gap-2">
          <span className="stamp text-[10px] text-[var(--color-signal)]">CACHING SNAPSHOT</span>
          <Loader2 size={11} className="animate-spin text-ink-8" />
        </div>
        <div className="text-[13px] text-ink-11 mt-1">
          Downloading <span className="font-medium">{season}</span> stats from Sleeper.
          First time only — about 15 seconds.
        </div>
      </div>
      <div className="absolute bottom-0 left-0 h-[2px] bg-[var(--color-signal)]/40 animate-pulse" style={{ width: '100%' }} />
    </div>
  )
}
