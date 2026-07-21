import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, GraduationCap, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { Combobox } from './Combobox'

// One-click demo login for graders. Loads the Footballguys account and its
// party league so a TA doesn't need a Sleeper account to see the app work.
const DEMO_USERNAME = 'footballguys'
const DEMO_LEAGUE_ID = '1182163805001936896'

/**
 * Cold-start screen. Three required fields: username, league ID, season.
 * Username + season pre-flight a lookup for league names so the league
 * field gets a hint of which league IDs are real for that user.
 */
export function EntryForm({
  onSubmit,
}: {
  onSubmit: (args: { username: string; leagueId: string; season: number }) => void
}) {
  const [username, setUsername] = useState('')
  const [leagueId, setLeagueId] = useState('')
  const [season, setSeason] = useState<number>(() => {
    const now = new Date().getUTCFullYear()
    // Default to the most recent fully-completed NFL season: if we're
    // before September, last year is most recent; otherwise this year.
    return new Date().getUTCMonth() < 8 ? now - 1 : now
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Pull state (live season) so the picker offers a sane range.
  const stateQ = useQuery({ queryKey: ['state'], queryFn: api.state, staleTime: 5 * 60_000 })

  const seasonOptions = useMemo(() => {
    const top = stateQ.data ? Math.max(stateQ.data.season, season) : season
    const out: number[] = []
    for (let s = top; s >= top - 7; s--) out.push(s)
    return out
  }, [stateQ.data, season])

  // Username lookup is a friendly assist — fills league dropdown choices.
  const lookupQ = useQuery({
    enabled: !!username.trim() && !!season,
    queryKey: ['lookup', username.trim(), season],
    queryFn: () => api.userLeagues(username.trim(), season),
    retry: false,
    staleTime: 60_000,
  })

  // If lookup returns exactly one league and the user hasn't typed one
  // yet, pre-fill it. They can still edit.
  useEffect(() => {
    if (lookupQ.data?.leagues.length === 1 && !leagueId) {
      setLeagueId(lookupQ.data.leagues[0].league_id)
    }
  }, [lookupQ.data, leagueId])

  const leagueOptions = useMemo(
    () =>
      (lookupQ.data?.leagues ?? []).map((lg) => ({
        value: lg.league_id,
        label: `${lg.name} · ${lg.league_id.slice(-6)}`,
      })),
    [lookupQ.data],
  )

  function submit() {
    setError(null)
    const u = username.trim()
    const lid = leagueId.trim()
    if (!u) return setError('Username is required.')
    if (!lid) return setError('League ID is required.')
    if (!season) return setError('Pick a season.')
    setBusy(true)
    onSubmit({ username: u, leagueId: lid, season })
  }

  // Skip the form entirely and load the Footballguys demo league.
  function loadDemo() {
    setError(null)
    setBusy(true)
    onSubmit({ username: DEMO_USERNAME, leagueId: DEMO_LEAGUE_ID, season })
  }

  return (
    <div className="relative min-h-screen flex items-center justify-center p-8">
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            'radial-gradient(ellipse 600px 400px at 50% 30%, color-mix(in oklch, var(--color-signal) 8%, transparent), transparent 70%)',
        }}
      />

      <div className="relative w-full max-w-xl rise-in">
        <div className="flex items-center gap-2 mb-7">
          <span className="stamp text-[10px] text-ink-8">FOOTBALL GENIE</span>
        </div>

        <h1 className="display text-6xl text-ink-12 mb-4">
          Don't lose sleep
          <br />
          because of <span className="text-[var(--color-signal)]">Sleeper</span>.
        </h1>
        <p className="text-ink-8 text-base mb-8 max-w-md">
          Sleeper rosters, weekly projections, and a variance-adjusted
          recommendation for every slot.
        </p>

        <button
          onClick={loadDemo}
          disabled={busy}
          className={cn(
            'group mb-8 w-full flex items-center justify-between gap-3 px-5 py-3.5 rounded-md transition text-left',
            'bg-ink-2 border hairline hover:bg-ink-3 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed',
          )}
        >
          <span className="flex items-center gap-3">
            <span className="grid place-items-center w-8 h-8 rounded-md bg-ink-3 border hairline shrink-0">
              <GraduationCap size={15} className="text-[var(--color-signal)]" />
            </span>
            <span className="flex flex-col leading-tight">
              <span className="stamp text-[11px] text-ink-12">FOR TAs / INSTRUCTORS</span>
              <span className="text-[12px] text-ink-8">
                One-click load of the Footballguys demo league
              </span>
            </span>
          </span>
          {busy ? (
            <Loader2 size={14} className="animate-spin text-ink-8 shrink-0" />
          ) : (
            <ArrowRight size={14} className="text-ink-8 group-hover:translate-x-0.5 transition shrink-0" />
          )}
        </button>

        <div className="space-y-5">
          <Field label="SLEEPER USERNAME">
            <input
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
              placeholder="footballguys"
              className="w-full bg-ink-2 border hairline rounded-md px-3.5 py-2.5 text-[15px] text-ink-12 placeholder:text-ink-7 focus:outline-none focus:ring-2 focus:ring-[var(--color-signal)]/30 transition cursor-text"
            />
          </Field>

          <div className="grid grid-cols-[1fr_180px] gap-3">
            <Field
              label={
                <div className="flex items-center gap-2">
                  <span>LEAGUE ID</span>
                  {lookupQ.isFetching && (
                    <Loader2 size={10} className="animate-spin text-ink-7" />
                  )}
                </div>
              }
            >
              {leagueOptions.length > 0 ? (
                <Combobox
                  value={leagueId}
                  onChange={setLeagueId}
                  options={leagueOptions}
                  placeholder="Pick a league…"
                />
              ) : (
                <input
                  value={leagueId}
                  onChange={(e) => setLeagueId(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && submit()}
                  placeholder="paste your league id"
                  className="w-full bg-ink-2 border hairline rounded-md px-3.5 py-2.5 text-[14px] text-ink-12 placeholder:text-ink-7 focus:outline-none focus:ring-2 focus:ring-[var(--color-signal)]/30 transition cursor-text"
                />
              )}
            </Field>

            <Combobox
              label="SEASON"
              value={String(season)}
              onChange={(v) => {
                setSeason(parseInt(v, 10))
                setLeagueId('') // force re-lookup for new season
              }}
              options={seasonOptions.map((s) => ({ value: String(s), label: String(s) }))}
            />
          </div>

          <button
            onClick={submit}
            disabled={busy || !username.trim() || !leagueId.trim()}
            className={cn(
              'group w-full flex items-center justify-center gap-2 px-5 py-3 rounded-md transition',
              'bg-[var(--color-signal)] text-white font-medium cursor-pointer',
              'hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed',
            )}
          >
            <span className="stamp text-[11px]">LOAD LINEUP</span>
            {busy ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <ArrowRight size={14} className="group-hover:translate-x-0.5 transition" />
            )}
          </button>

          {error && (
            <div className="rounded-md border border-[var(--color-signal)]/40 bg-[var(--color-signal)]/8 px-4 py-3 text-sm text-ink-11">
              <span className="stamp text-[10px] text-[var(--color-signal)] mr-2">ERROR</span>
              {error}
            </div>
          )}

          {lookupQ.error && username.trim() && (
            <div className="text-xs text-ink-7">
              <span className="stamp text-[10px] text-ink-7 mr-2">LOOKUP</span>
              Couldn't fetch leagues for{' '}
              <span className="text-ink-11">{username}</span> in {season}. You can
              still paste a league ID manually.
            </div>
          )}
        </div>

        <div className="mt-14 flex items-center justify-between text-[10px] stamp text-ink-7">
          <span>FOOTBALL GENIE · v0.1</span>
          <span>SLEEPER / NFL / {stateQ.data?.season ?? '—'}</span>
        </div>
      </div>
    </div>
  )
}

function Field({
  label,
  children,
}: {
  label: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="stamp text-[10px] text-ink-7 leading-none">{label}</span>
      {children}
    </div>
  )
}
