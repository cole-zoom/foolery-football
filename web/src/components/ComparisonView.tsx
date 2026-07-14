import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Swords, TrendingUp } from 'lucide-react'
import { api, ApiError, type Availability, type Comparison, type ComparisonPlayer, type Model, type Pool } from '@/lib/api'
import { PlayerAvatar } from '@/components/PlayerAvatar'
import { PositionChip } from '@/components/PositionChip'
import { cn } from '@/lib/cn'

/**
 * Model-vs-human retrospective for one completed week.
 *
 * Left: the lineup the model would have fielded (leakage-safe replay)
 * against the lineup the manager actually started, both scored by what
 * really happened. Right: the model's report card — per-player
 * predicted-vs-actual errors.
 *
 * Error-bar colors are the validated amber/blue diverging pair
 * (--color-pos-qb / --color-pos-wr): direction off the zero axis is the
 * primary encoding, color + legend are reinforcement.
 */

const OVER_COLOR = 'var(--color-pos-qb)' // amber — model over-projected
const UNDER_COLOR = 'var(--color-pos-wr)' // blue — model under-projected

const POOL_PHRASE: Record<Pool, string> = {
  roster: 'your roster',
  both: 'your roster plus that week’s waivers',
  waivers: 'that week’s waivers only',
}

export function ComparisonView({
  user,
  leagueId,
  season,
  week,
  model,
  risk,
  pool,
  availability,
  onViewPlayer,
}: {
  user: string
  leagueId: string
  season: number
  week: number | null
  model: Model
  risk: number
  pool: Pool
  availability: Availability
  onViewPlayer: (playerId: string) => void
}) {
  const q = useQuery({
    enabled: week !== null,
    queryKey: ['comparison', leagueId, user, season, week, model, risk, pool, availability],
    queryFn: () =>
      api.comparison({
        league_id: leagueId,
        user,
        season,
        week: week ?? undefined,
        model,
        risk,
        pool,
        availability,
      }),
    placeholderData: (prev) => prev,
    retry: (count, err) => !(err instanceof ApiError && err.status === 400) && count < 2,
  })

  const data = q.data

  return (
    <main className="flex-1 max-w-[1400px] mx-auto w-full px-8 py-10">
      <div className="mb-7 flex items-end justify-between gap-6 flex-wrap">
        <div>
          <div className="stamp text-[10px] text-ink-7 mb-2">MODEL VS YOU · HINDSIGHT</div>
          <h2 className="display text-[36px] text-ink-12">
            Week <span className="text-[var(--color-signal)]">{week ?? '—'}</span> · {season}
          </h2>
          <p className="text-ink-8 text-sm mt-2 max-w-md leading-relaxed">
            What the <span className="text-ink-11">{model}</span> model would have started
            (seeing only weeks before this one, picking from{' '}
            <span className="text-ink-11">{POOL_PHRASE[pool]}</span>) versus what you
            actually fielded — both scored by what really happened.
          </p>
        </div>
        {data && <VerdictCard data={data} loading={q.isFetching} />}
      </div>

      {q.error instanceof ApiError && q.error.status === 400 ? (
        <InfoBanner
          label="NOT COMPARABLE YET"
          message={q.error.detail ?? 'This week has no recorded stats yet — pick a completed week.'}
        />
      ) : q.error ? (
        <InfoBanner label="ERROR" message={(q.error as Error).message} tone="error" />
      ) : null}

      {data?.using_prior_season && data.prior_season && (
        <InfoBanner
          label={`WEEK ${data.week}`}
          message={`No ${data.season} games preceded this week, so the model predicted from the full ${data.prior_season} season.`}
        />
      )}

      {q.isLoading && week !== null && (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-[68px] rounded-md bg-ink-2 border hairline animate-pulse" />
          ))}
        </div>
      )}

      {data && (
        <div className={cn('grid grid-cols-1 lg:grid-cols-[1fr_440px] gap-10', q.isFetching && 'opacity-70')}>
          <section>
            <div className="grid grid-cols-[1fr_auto_1fr] items-center px-4 pb-2">
              <span className="stamp text-[10px] text-[var(--color-signal)]">MODEL'S LINEUP</span>
              <span />
              <span className="stamp text-[10px] text-[var(--color-good)] text-right">YOUR LINEUP</span>
            </div>
            <div className="space-y-2">
              {data.slots.map((s) => (
                <SlotRow key={s.slot_id} slot={s} onViewPlayer={onViewPlayer} />
              ))}
            </div>
          </section>

          <aside className="space-y-6">
            <ScoreboardCard data={data} />
            <AccuracyCard data={data} onViewPlayer={onViewPlayer} />
          </aside>
        </div>
      )}
    </main>
  )
}

/* === Verdict + totals === */

function VerdictCard({ data, loading }: { data: Comparison; loading: boolean }) {
  const delta = data.totals.model_actual - data.totals.human_actual
  const modelWon = delta > 0.05
  const youWon = delta < -0.05
  return (
    <div className={cn('bg-ink-2 border hairline rounded-lg px-5 py-4 transition', loading && 'opacity-70')}>
      <div className="flex items-center gap-2 mb-1">
        <Swords size={11} className="text-[var(--color-signal)]" />
        <span className="stamp text-[10px] text-ink-7">WEEK VERDICT</span>
        {loading && <Loader2 size={10} className="animate-spin text-ink-8" />}
      </div>
      <div className="flex items-baseline gap-2">
        <span
          className="nums display text-4xl font-bold leading-none"
          style={{ color: modelWon ? 'var(--color-signal-soft)' : youWon ? 'var(--color-good)' : 'var(--color-ink-12)' }}
        >
          {modelWon ? 'MODEL' : youWon ? 'YOU' : 'EVEN'}
        </span>
        {(modelWon || youWon) && (
          <span className="nums text-lg text-ink-11 font-semibold">+{Math.abs(delta).toFixed(1)}</span>
        )}
      </div>
      <div className="mt-1.5 stamp text-[10px] text-ink-8">
        {modelWon
          ? 'THE MODEL WOULD HAVE OUTSCORED YOUR LINEUP'
          : youWon
            ? 'YOUR CALLS BEAT THE MODEL THIS WEEK'
            : 'SAME TOTAL EITHER WAY'}
      </div>
    </div>
  )
}

function ScoreboardCard({ data }: { data: Comparison }) {
  const t = data.totals
  const benched = t.perfect_actual !== null ? t.perfect_actual - t.human_actual : null
  return (
    <div className="bg-ink-2 border hairline rounded-lg p-6 rise-in">
      <div className="flex items-center gap-2 mb-4">
        <TrendingUp size={11} className="text-[var(--color-signal)]" />
        <span className="stamp text-[10px] text-ink-7">ACTUAL POINTS SCORED</span>
      </div>
      <div className="grid grid-cols-3 gap-4">
        <TotalStat label="MODEL" value={t.model_actual} sub={`PROJ ${t.model_predicted.toFixed(1)}`} color="var(--color-signal-soft)" />
        <TotalStat
          label="YOU"
          value={t.human_actual}
          sub={t.human_predicted !== null ? `PROJ ${t.human_predicted.toFixed(1)}` : undefined}
          color="var(--color-good)"
        />
        <TotalStat label="PERFECT" value={t.perfect_actual} sub="WITH HINDSIGHT" />
      </div>
      {benched !== null && benched > 0.05 && (
        <div className="mt-4 pt-4 border-t hairline stamp text-[10px] text-ink-8">
          YOU LEFT <span className="text-ink-11">{benched.toFixed(1)} PTS</span> ON THE BENCH
        </div>
      )}
    </div>
  )
}

function TotalStat({
  label,
  value,
  sub,
  color,
  digits = 1,
}: {
  label: string
  value: number | null
  sub?: string
  color?: string
  digits?: number
}) {
  return (
    <div>
      <div className="nums text-2xl font-bold leading-none" style={{ color: color ?? 'var(--color-ink-12)' }}>
        {value !== null ? value.toFixed(digits) : '—'}
      </div>
      <div className="stamp text-[9px] text-ink-7 mt-1.5">{label}</div>
      {sub && <div className="stamp text-[9px] text-ink-6 mt-0.5">{sub}</div>}
    </div>
  )
}

/* === Per-slot model-vs-you rows === */

function SlotRow({
  slot,
  onViewPlayer,
}: {
  slot: Comparison['slots'][number]
  onViewPlayer: (playerId: string) => void
}) {
  const modelActual = slot.model_pick?.actual_points ?? null
  const starterActual = slot.actual_starter?.actual_points ?? null
  const delta = (modelActual ?? 0) - (starterActual ?? 0)

  let badge: { text: string; color: string } | null = null
  if (slot.same_player) {
    badge = { text: 'MATCH', color: 'var(--color-ink-8)' }
  } else if (delta > 0.05) {
    badge = { text: `MODEL +${delta.toFixed(1)}`, color: 'var(--color-signal-soft)' }
  } else if (delta < -0.05) {
    badge = { text: `YOU +${Math.abs(delta).toFixed(1)}`, color: 'var(--color-good)' }
  } else {
    badge = { text: 'EVEN', color: 'var(--color-ink-8)' }
  }

  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 py-3 bg-ink-2 border hairline rounded-md">
      <PlayerCell entry={slot.model_pick} side="left" onViewPlayer={onViewPlayer} />
      <div className="flex flex-col items-center gap-1.5 w-[104px]">
        <PositionChip position={slot.slot} size="xs" />
        <span
          className="stamp text-[10px] whitespace-nowrap"
          style={{ color: badge.color }}
          title={
            slot.same_player
              ? 'The model would have started the same player you did'
              : 'Difference in actual points between the two picks'
          }
        >
          {badge.text}
        </span>
      </div>
      <PlayerCell entry={slot.actual_starter} side="right" onViewPlayer={onViewPlayer} />
    </div>
  )
}

function PlayerCell({
  entry,
  side,
  onViewPlayer,
}: {
  entry: ComparisonPlayer | null
  side: 'left' | 'right'
  onViewPlayer: (playerId: string) => void
}) {
  if (!entry) {
    return (
      <div className={cn('stamp text-[10px] text-ink-6', side === 'right' && 'text-right')}>
        EMPTY
      </div>
    )
  }
  const p = entry.player
  const actual = entry.actual_points
  const nameBlock = (
    <div className={cn('min-w-0', side === 'right' && 'text-right')}>
      <div className="text-[13px] text-ink-12 font-medium truncate">{p.full_name}</div>
      <div className="stamp text-[9px] text-ink-7 mt-0.5">
        {entry.predicted_mean !== null ? `PROJ ${entry.predicted_mean.toFixed(1)}` : 'NO PROJ'}
        {' · '}
        <span className={cn(actual === null && 'text-[var(--color-warn)]')} title={actual === null ? 'Did not play' : undefined}>
          {actual !== null ? `ACT ${actual.toFixed(1)}` : 'DNP'}
        </span>
      </div>
    </div>
  )
  return (
    <button
      onClick={() => onViewPlayer(p.player_id)}
      className={cn(
        'flex items-center gap-3 min-w-0 text-left rounded-md px-1 py-0.5 -mx-1 hover:bg-ink-3 transition',
        side === 'right' && 'flex-row-reverse text-right',
      )}
    >
      <PlayerAvatar player={p} size={36} />
      {nameBlock}
    </button>
  )
}

/* === Model report card === */

function AccuracyCard({
  data,
  onViewPlayer,
}: {
  data: Comparison
  onViewPlayer: (playerId: string) => void
}) {
  const { compared, excluded, maxAbs } = useMemo(() => {
    const compared: { row: ComparisonPlayer; error: number }[] = []
    const excluded: ComparisonPlayer[] = []
    for (const row of data.roster) {
      if (row.predicted_mean !== null && row.actual_points !== null) {
        compared.push({ row, error: row.predicted_mean - row.actual_points })
      } else {
        excluded.push(row)
      }
    }
    compared.sort((a, b) => Math.abs(b.error) - Math.abs(a.error))
    const maxAbs = compared.reduce((m, c) => Math.max(m, Math.abs(c.error)), 0)
    return { compared, excluded, maxAbs }
  }, [data.roster])

  const bias = data.accuracy.mean_error

  return (
    <div className="bg-ink-2 border hairline rounded-lg p-6">
      <div className="stamp text-[10px] text-ink-7 mb-4">MODEL REPORT CARD</div>

      <div className="grid grid-cols-3 gap-4 mb-5">
        <TotalStat label="AVG MISS (MAE)" value={data.accuracy.mae} sub="PTS / PLAYER" />
        <TotalStat
          label={bias !== null && bias < 0 ? 'BIAS · UNDER' : 'BIAS · OVER'}
          value={bias !== null ? Math.abs(bias) : null}
          sub="SIGNED AVG"
          color={bias !== null ? (bias >= 0 ? OVER_COLOR : UNDER_COLOR) : undefined}
        />
        <TotalStat label="PLAYERS" value={data.accuracy.n} sub="COMPARED" digits={0} />
      </div>

      <div className="flex items-center gap-4 mb-3">
        <LegendSwatch color={OVER_COLOR} label="OVER-PROJECTED" />
        <LegendSwatch color={UNDER_COLOR} label="UNDER-PROJECTED" />
      </div>

      <div className="space-y-1">
        {compared.map(({ row, error }) => (
          <ErrorRow key={row.player.player_id} row={row} error={error} maxAbs={maxAbs} onViewPlayer={onViewPlayer} />
        ))}
        {compared.length === 0 && (
          <div className="stamp text-[10px] text-ink-6 py-2">NOTHING TO COMPARE THIS WEEK</div>
        )}
      </div>

      {excluded.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer stamp text-[10px] text-ink-7 hover:text-ink-11 select-none">
            NOT COMPARABLE ({excluded.length})
          </summary>
          <div className="mt-2 space-y-1">
            {excluded.map((row) => (
              <div key={row.player.player_id} className="flex items-center gap-2 px-1 py-1">
                <span className="text-[12px] text-ink-9 truncate flex-1">{row.player.full_name}</span>
                <span className="stamp text-[9px] text-ink-6">
                  {row.actual_points === null ? 'DID NOT PLAY' : 'NO PROJECTION'}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

function ErrorRow({
  row,
  error,
  maxAbs,
  onViewPlayer,
}: {
  row: ComparisonPlayer
  error: number
  maxAbs: number
  onViewPlayer: (playerId: string) => void
}) {
  const over = error >= 0
  const widthPct = maxAbs > 0 ? (Math.abs(error) / maxAbs) * 50 : 0
  return (
    <button
      onClick={() => onViewPlayer(row.player.player_id)}
      className="w-full grid grid-cols-[110px_1fr_44px] items-center gap-2 px-1 py-1 rounded-sm hover:bg-ink-3 transition text-left"
      title={`${row.player.full_name}: projected ${row.predicted_mean!.toFixed(1)}, actual ${row.actual_points!.toFixed(1)}`}
    >
      <span className="text-[12px] text-ink-10 truncate">{row.player.full_name}</span>
      <span className="relative h-[6px]">
        <span className="absolute inset-y-0 left-1/2 w-px bg-ink-6" />
        <span
          className="absolute inset-y-0 rounded-xs"
          style={{
            background: over ? OVER_COLOR : UNDER_COLOR,
            width: `${widthPct}%`,
            left: over ? '50%' : `${50 - widthPct}%`,
          }}
        />
      </span>
      <span className="nums text-[11px] text-ink-9 text-right">
        {over ? '+' : '−'}{Math.abs(error).toFixed(1)}
      </span>
    </button>
  )
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="w-2 h-2 rounded-xs" style={{ background: color }} />
      <span className="stamp text-[9px] text-ink-7">{label}</span>
    </span>
  )
}

/* === Banners === */

function InfoBanner({
  label,
  message,
  tone = 'info',
}: {
  label: string
  message: string
  tone?: 'info' | 'error'
}) {
  return (
    <div
      className={cn(
        'rounded-md border px-4 py-3 mb-3 text-sm text-ink-11',
        tone === 'error'
          ? 'border-[var(--color-signal)]/40 bg-[var(--color-signal)]/8'
          : 'hairline bg-ink-2',
      )}
    >
      <span
        className={cn('stamp text-[10px] mr-2', tone === 'error' ? 'text-[var(--color-signal)]' : 'text-ink-7')}
      >
        {label}
      </span>
      {message}
    </div>
  )
}
