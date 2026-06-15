import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '@/lib/api'
import { positionColor } from '@/lib/positions'
import { PlayerAvatar } from './PlayerAvatar'
import { Sheet } from './Sheet'
import { StatNumber } from './StatNumber'

/**
 * Player detail drawer.
 * Shows weekly points bar chart, season aggregates, and the why-behind-
 * the-pick reasoning (variance, confidence, notes).
 */
export function PlayerDrawer({
  playerId,
  leagueId,
  season,
  week,
  onClose,
}: {
  playerId: string | null
  leagueId: string
  season: number
  week: number | null
  onClose: () => void
}) {
  const q = useQuery({
    enabled: !!playerId,
    queryKey: ['player-stats', playerId, leagueId, season, week],
    queryFn: () => api.playerStats(playerId!, leagueId, season, week ?? undefined),
  })

  const p = q.data?.player
  const color = positionColor(p?.position)

  return (
    <Sheet
      open={!!playerId}
      onOpenChange={(open) => !open && onClose()}
      subtitle={p ? `${p.position} · ${p.team ?? '—'}` : 'LOADING'}
      title={p?.full_name ?? '…'}
      width="xl"
    >
      <div className="px-7 py-6">
        {q.isLoading && (
          <div className="flex items-center justify-center py-20 gap-2 text-ink-8">
            <Loader2 size={14} className="animate-spin" />
            <span className="stamp text-[10px]">LOADING STATS</span>
          </div>
        )}

        {q.error && (
          <div className="rounded-md border border-[var(--color-signal)]/40 bg-[var(--color-signal)]/8 px-4 py-3 text-sm text-ink-11">
            <span className="stamp text-[10px] text-[var(--color-signal)] mr-2">ERROR</span>
            {(q.error as Error).message}
          </div>
        )}

        {q.data && p && (
          <>
            <div className="flex items-center gap-5 mb-8">
              <PlayerAvatar player={p} size={84} />
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className="stamp text-[10px]"
                    style={{ color: `color-mix(in oklch, ${color} 85%, white)` }}
                  >
                    {p.position}
                  </span>
                  <span className="text-ink-7 text-[10px]">·</span>
                  <span className="stamp text-[10px] text-ink-9">{p.team ?? '—'}</span>
                  {p.injury_status && (
                    <>
                      <span className="text-ink-7 text-[10px]">·</span>
                      <span className="stamp text-[10px] text-[var(--color-signal)]">
                        {p.injury_status}
                      </span>
                    </>
                  )}
                </div>
                <div className="stamp text-[10px] text-ink-7">
                  SEASON {q.data.using_prior_season && q.data.prior_season
                    ? `${q.data.prior_season} (PRIOR)`
                    : q.data.season}
                </div>
              </div>
            </div>

            {q.data.using_prior_season && q.data.prior_season && (
              <div className="mb-6 rounded-md border border-[var(--color-signal)]/40 bg-[var(--color-signal)]/8 px-4 py-3 text-sm text-ink-11">
                <span className="stamp text-[10px] text-[var(--color-signal)] mr-2">PRIOR SEASON</span>
                Showing {q.data.prior_season} weekly stats — no {q.data.season} games played yet.
              </div>
            )}

            <div className="grid grid-cols-4 gap-5 mb-10">
              <StatNumber value={q.data.season_total_points} label="TOTAL PTS" decimals={1} size="lg" />
              <StatNumber value={q.data.points_per_game} label="PPG" decimals={2} size="lg" />
              <StatNumber value={q.data.stddev} label="STDDEV" decimals={2} size="lg" tone="muted" />
              <StatNumber value={q.data.games_played} label="GAMES" decimals={0} size="lg" tone="muted" />
            </div>

            <div className="mb-3 flex items-baseline justify-between">
              <span className="stamp text-[10px] text-ink-8">WEEKLY POINTS</span>
              <span className="stamp text-[10px] text-ink-7">MEAN ± STDDEV REFERENCE</span>
            </div>

            <div className="bg-ink-2 border hairline rounded-md p-4 mb-8">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={q.data.weeks} margin={{ top: 8, right: 8, left: -16, bottom: 4 }}>
                  <CartesianGrid stroke="var(--color-ink-4)" strokeDasharray="2 4" vertical={false} />
                  <XAxis
                    dataKey="week"
                    tick={{ fill: 'var(--color-ink-7)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                    axisLine={{ stroke: 'var(--color-ink-5)' }}
                    tickLine={false}
                    tickFormatter={(v) => `W${v}`}
                  />
                  <YAxis
                    tick={{ fill: 'var(--color-ink-7)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                    axisLine={false}
                    tickLine={false}
                    width={40}
                  />
                  <Tooltip
                    cursor={{ fill: 'var(--color-ink-4)' }}
                    contentStyle={{
                      background: 'var(--color-ink-3)',
                      border: '1px solid var(--color-ink-6)',
                      borderRadius: 6,
                      fontFamily: 'var(--font-mono)',
                      fontSize: 11,
                    }}
                    labelFormatter={(v) => `WEEK ${v}`}
                    formatter={(v) => [`${Number(v).toFixed(2)} pts`, 'points']}
                  />
                  <ReferenceLine
                    y={q.data.points_per_game}
                    stroke="var(--color-ink-7)"
                    strokeDasharray="3 3"
                    label={{
                      value: `MEAN ${q.data.points_per_game.toFixed(1)}`,
                      fill: 'var(--color-ink-8)',
                      fontSize: 9,
                      position: 'insideTopRight',
                      fontFamily: 'var(--font-mono)',
                    }}
                  />
                  <Bar dataKey="points" radius={[3, 3, 0, 0]} fill={color} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="mb-8">
              <div className="stamp text-[10px] text-ink-8 mb-3">REASONING</div>
              <div className="bg-ink-2 border hairline rounded-md p-5 space-y-2 text-sm text-ink-10 leading-relaxed">
                <p>
                  Average of <span className="nums text-ink-12">{q.data.points_per_game.toFixed(2)}</span> points per
                  game over <span className="nums text-ink-12">{q.data.games_played}</span> appearances. Week-to-week
                  swing of <span className="nums text-ink-12">±{q.data.stddev.toFixed(2)}</span> (sample stddev) puts
                  this player roughly between{' '}
                  <span className="nums text-ink-12">
                    {Math.max(0, q.data.points_per_game - q.data.stddev).toFixed(1)}
                  </span>{' '}
                  and{' '}
                  <span className="nums text-ink-12">
                    {(q.data.points_per_game + q.data.stddev).toFixed(1)}
                  </span>{' '}
                  on a typical Sunday.
                </p>
                <p className="text-ink-8 text-xs pt-1">
                  The recommendation engine adjusts this baseline by your risk slider — a high‑variance
                  player climbs the rank when you slide toward <em>aggressive</em>, and falls when you slide
                  toward <em>safe</em>.
                </p>
              </div>
            </div>

            <details className="bg-ink-2 border hairline rounded-md">
              <summary className="cursor-pointer px-4 py-3 stamp text-[10px] text-ink-8 hover:text-ink-11 select-none">
                RAW WEEKLY STAT LINES
              </summary>
              <div className="border-t hairline divide-y divide-[var(--color-ink-4)] max-h-72 overflow-y-auto">
                {q.data.weeks.map((w) => (
                  <div key={w.week} className="px-4 py-3 grid grid-cols-[60px_80px_1fr] items-baseline gap-3 text-xs">
                    <span className="stamp text-ink-7">WK {w.week}</span>
                    <span className="nums text-ink-12 font-semibold">{w.points.toFixed(2)}</span>
                    <span className="text-ink-8 truncate">
                      {Object.entries(w.stats)
                        .filter(([k]) => !k.startsWith('bonus') && !['gp', 'gs', 'gms_active'].includes(k))
                        .slice(0, 6)
                        .map(([k, v]) => (
                          <span key={k} className="mr-2.5">
                            <span className="stamp text-[9px] text-ink-7">{k.toUpperCase()}</span>{' '}
                            <span className="nums">{v}</span>
                          </span>
                        ))}
                    </span>
                  </div>
                ))}
              </div>
            </details>
          </>
        )}
      </div>
    </Sheet>
  )
}
