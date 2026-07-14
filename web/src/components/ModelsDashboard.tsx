import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueries, useQueryClient } from '@tanstack/react-query'
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'
import { Loader2 } from 'lucide-react'
import { api, ApiError, MODELS, type Comparison, type Model, type Pool } from '@/lib/api'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/cn'

/**
 * Season-long model bench: every scoring model's weekly lineup total
 * charted against what the manager actually fielded, plus the
 * hindsight-perfect lineup as a benchmark.
 *
 * One /comparison call per (model, week), fetched through a 2-worker
 * pool (same reasoning as the /decisions prefetch: the API runs on a
 * single vCPU, so a fan-out would starve interactive requests). Query
 * keys match ComparisonView's exactly, so the two views share a cache.
 *
 * Series colors are a validated categorical palette (dataviz six
 * checks, both surfaces): fixed per entity, never re-assigned when
 * lines are toggled. "Perfect" is a neutral dashed benchmark, not a
 * categorical slot.
 */

type Metric = 'actual' | 'predicted'

type SeriesDef = {
  key: string
  label: string
  color: string
  dashed?: boolean
  model?: Model
}

const SERIES: SeriesDef[] = [
  ...MODELS.map((m, i) => ({
    key: m.value,
    label: m.label,
    color: ['#7c3aed', '#0284c7', '#d97706'][i] ?? '#db2777',
    model: m.value,
  })),
  { key: 'you', label: 'You (actual lineup)', color: '#059669' },
  { key: 'perfect', label: 'Perfect (hindsight)', color: 'var(--color-ink-8)', dashed: true },
]

const CHART_CONFIG: ChartConfig = Object.fromEntries(
  SERIES.map((s) => [s.key, { label: s.label, color: s.color }]),
)

const REGULAR_SEASON_LAST_WEEK = 18
const POOL_CONCURRENCY = 2
const STALE_MS = 5 * 60 * 1000

const round1 = (n: number) => Math.round(n * 10) / 10

export function ModelsDashboard({
  user,
  leagueId,
  season,
  risk,
  pool,
}: {
  user: string
  leagueId: string
  season: number
  risk: number
  pool: Pool
}) {
  const queryClient = useQueryClient()
  const [metric, setMetric] = useState<Metric>('actual')
  // null = every week; kept as strings because that's what ToggleGroup speaks.
  const [weekSel, setWeekSel] = useState<string[] | null>(null)
  const [visibleKeys, setVisibleKeys] = useState<string[]>(SERIES.map((s) => s.key))

  const stateQ = useQuery({ queryKey: ['state'], queryFn: api.state })

  // Only completed weeks are comparable: past seasons run 1..18, the
  // live season stops at the week before the current one.
  const maxWeek = stateQ.data
    ? season < stateQ.data.season
      ? REGULAR_SEASON_LAST_WEEK
      : Math.max(1, Math.min(REGULAR_SEASON_LAST_WEEK, stateQ.data.week - 1))
    : 0
  const allWeeks = useMemo(
    () => Array.from({ length: maxWeek }, (_, i) => i + 1),
    [maxWeek],
  )

  // Model-major order; results[mi * allWeeks.length + wi] below relies on it.
  const combos = useMemo(
    () => MODELS.flatMap((m) => allWeeks.map((week) => ({ model: m.value, week }))),
    [allWeeks],
  )

  // Disabled observers: they never fetch themselves, they just watch the
  // cache the worker pool fills. Key shape mirrors ComparisonView.
  const results = useQueries({
    queries: combos.map((c) => ({
      queryKey: ['comparison', leagueId, user, season, c.week, c.model, risk, pool] as const,
      queryFn: () =>
        api.comparison({
          league_id: leagueId,
          user,
          season,
          week: c.week,
          model: c.model,
          risk,
          pool,
        }),
      enabled: false,
      staleTime: STALE_MS,
    })),
  })

  useEffect(() => {
    if (maxWeek === 0) return
    // Week-major queue so the chart grows a whole week at a time.
    const queue: { model: Model; week: number }[] = []
    for (let w = 1; w <= maxWeek; w++) {
      for (const m of MODELS) queue.push({ model: m.value, week: w })
    }
    let cancelled = false
    const worker = async () => {
      while (!cancelled) {
        const c = queue.shift()
        if (!c) return
        await queryClient.prefetchQuery({
          queryKey: ['comparison', leagueId, user, season, c.week, c.model, risk, pool],
          queryFn: () =>
            api.comparison({
              league_id: leagueId,
              user,
              season,
              week: c.week,
              model: c.model,
              risk,
              pool,
            }),
          staleTime: STALE_MS,
          retry: (count, err) =>
            !(err instanceof ApiError && err.status === 400) && count < 2,
          retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
        })
      }
    }
    for (let i = 0; i < POOL_CONCURRENCY; i++) void worker()
    return () => {
      cancelled = true
    }
  }, [queryClient, leagueId, user, season, risk, pool, maxWeek])

  const settled = results.filter((r) => r.isSuccess || r.isError).length
  const loading = combos.length === 0 || settled < combos.length
  const allFailed = combos.length > 0 && settled === combos.length && results.every((r) => r.isError)
  const firstError = results.find((r) => r.error)?.error as Error | undefined

  const selectedWeeks = useMemo(() => {
    if (weekSel === null) return allWeeks
    const set = new Set(weekSel.map(Number))
    return allWeeks.filter((w) => set.has(w))
  }, [weekSel, allWeeks])

  const rows = useMemo(() => {
    const out: Array<Record<string, number | null>> = []
    for (const week of selectedWeeks) {
      const row: Record<string, number | null> = { week }
      let human: Comparison['totals'] | null = null
      let anySettled = false
      MODELS.forEach((m, mi) => {
        const q = results[mi * allWeeks.length + (week - 1)]
        if (q?.isSuccess || q?.isError) anySettled = true
        const t = q?.data?.totals ?? null
        if (t && !human) human = t
        row[m.value] = t
          ? round1(metric === 'actual' ? t.model_actual : t.model_predicted)
          : null
      })
      const h = human as Comparison['totals'] | null
      row.you = h
        ? metric === 'actual'
          ? round1(h.human_actual)
          : h.human_predicted !== null
            ? round1(h.human_predicted)
            : null
        : null
      row.perfect =
        metric === 'actual' && h?.perfect_actual != null ? round1(h.perfect_actual) : null
      // A week where every model settled with an error (400: no stats)
      // isn't comparable — drop it rather than chart a hole.
      const allNull = SERIES.every((s) => row[s.key] === null || s.key === 'perfect')
      if (!(allNull && anySettled && !loading)) out.push(row)
    }
    return out
  }, [selectedWeeks, results, allWeeks, metric, loading])

  // Perfect has no "predicted" reading — it only exists in hindsight.
  const activeSeries = SERIES.filter(
    (s) => visibleKeys.includes(s.key) && !(metric === 'predicted' && s.key === 'perfect'),
  )

  const stats = useMemo(
    () =>
      activeSeries.map((s) => {
        let total = 0
        let n = 0
        let wins = 0
        let winnable = 0
        for (const row of rows) {
          const v = row[s.key]
          if (v === null || v === undefined) continue
          total += v
          n += 1
          const you = row.you
          if (s.model && you !== null && you !== undefined) {
            winnable += 1
            if (v > you + 0.05) wins += 1
          }
        }
        return { series: s, total, n, avg: n > 0 ? total / n : null, wins, winnable }
      }),
    [activeSeries, rows],
  )

  const anyPriorSeason = results.some((r) => r.data?.using_prior_season)

  if (stateQ.isLoading || (combos.length > 0 && settled === 0 && !allFailed)) {
    return (
      <main className="flex-1 max-w-[1400px] mx-auto w-full px-8 py-10">
        <PageHeader season={season} pool={pool} risk={risk} />
        <ProgressNote settled={settled} total={combos.length} />
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-[92px]" />
          ))}
        </div>
        <Skeleton className="h-[480px]" />
      </main>
    )
  }

  return (
    <main className="flex-1 max-w-[1400px] mx-auto w-full px-8 py-10">
      <PageHeader season={season} pool={pool} risk={risk} />

      {allFailed && (
        <Card className="mb-6 border-[var(--color-signal)]/40 bg-[var(--color-signal)]/8 py-3">
          <CardContent className="text-sm text-ink-11">
            <span className="stamp text-[10px] text-[var(--color-signal)] mr-2">
              NOTHING TO CHART
            </span>
            {firstError instanceof ApiError && firstError.detail
              ? firstError.detail
              : (firstError?.message ?? 'No completed weeks are comparable yet.')}
          </CardContent>
        </Card>
      )}

      {loading && !allFailed && <ProgressNote settled={settled} total={combos.length} />}

      {/* Filter row: metric, then series visibility (doubles as the legend). */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 mb-4">
        <div className="flex flex-col gap-1.5">
          <span className="stamp text-[10px] text-ink-7 leading-none">SCORED BY</span>
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={metric}
            onValueChange={(v) => v && setMetric(v as Metric)}
          >
            <ToggleGroupItem value="actual" className="stamp text-[10px]">
              ACTUAL POINTS
            </ToggleGroupItem>
            <ToggleGroupItem value="predicted" className="stamp text-[10px]">
              PREDICTED POINTS
            </ToggleGroupItem>
          </ToggleGroup>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className="stamp text-[10px] text-ink-7 leading-none">SERIES</span>
          <ToggleGroup
            type="multiple"
            variant="outline"
            size="sm"
            spacing={1}
            value={visibleKeys}
            onValueChange={(v) => setVisibleKeys(v)}
          >
            {SERIES.map((s) => (
              <ToggleGroupItem
                key={s.key}
                value={s.key}
                disabled={metric === 'predicted' && s.key === 'perfect'}
                className="stamp text-[10px] gap-1.5 data-[state=off]:opacity-45"
              >
                <span
                  aria-hidden
                  className={cn('h-[3px] w-4 rounded-full shrink-0', s.dashed && 'opacity-70')}
                  style={{
                    background: s.dashed
                      ? `repeating-linear-gradient(90deg, ${s.color} 0 4px, transparent 4px 7px)`
                      : s.color,
                  }}
                />
                {s.label.split(' ')[0]}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
      </div>

      {/* Week filter — which weeks make the x-axis. */}
      <div className="flex flex-wrap items-end gap-3 mb-6">
        <div className="flex flex-col gap-1.5">
          <span className="stamp text-[10px] text-ink-7 leading-none">WEEKS</span>
          <ToggleGroup
            type="multiple"
            variant="outline"
            size="sm"
            spacing={1}
            className="flex-wrap"
            value={weekSel ?? allWeeks.map(String)}
            onValueChange={(v) => setWeekSel(v)}
          >
            {allWeeks.map((w) => (
              <ToggleGroupItem
                key={w}
                value={String(w)}
                className="nums text-[11px] min-w-8 data-[state=off]:opacity-45"
              >
                {w}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="stamp text-[10px] text-ink-8 h-8"
          onClick={() => setWeekSel(null)}
        >
          ALL
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="stamp text-[10px] text-ink-8 h-8"
          onClick={() => setWeekSel([])}
        >
          NONE
        </Button>
      </div>

      {/* Per-series season summary over the selected weeks. */}
      {stats.length > 0 && rows.length > 0 && (
        <div
          className="grid gap-4 mb-6"
          style={{ gridTemplateColumns: `repeat(${Math.min(stats.length, 5)}, minmax(0, 1fr))` }}
        >
          {stats.map(({ series, total, n, avg, wins, winnable }) => (
            <Card key={series.key} className="py-4 gap-2">
              <CardHeader className="px-4">
                <CardTitle className="stamp text-[10px] text-ink-7 font-medium flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className="h-2 w-2 rounded-[2px] shrink-0"
                    style={{ background: series.color }}
                  />
                  {series.label.split(' ')[0].toUpperCase()}
                </CardTitle>
              </CardHeader>
              <CardContent className="px-4">
                <div className="text-2xl font-bold text-ink-12 leading-none">
                  {n > 0 ? total.toFixed(1) : '—'}
                </div>
                <div className="stamp text-[9px] text-ink-7 mt-1.5">
                  {avg !== null ? `${avg.toFixed(1)} AVG / WK · ${n} WKS` : 'NO DATA'}
                </div>
                {series.model && winnable > 0 && (
                  <div className="stamp text-[9px] text-ink-8 mt-0.5">
                    BEAT YOU {wins}/{winnable} WKS
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="stamp text-[11px] text-ink-9 font-medium">
            LINEUP TOTAL BY WEEK
          </CardTitle>
          <CardDescription>
            {metric === 'actual'
              ? 'Points each lineup really scored — models replayed leakage-safe, you as fielded on Sleeper.'
              : 'Points each side was projected to score before kickoff.'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {rows.length === 0 || activeSeries.length === 0 ? (
            <div className="h-[420px] grid place-items-center">
              <span className="stamp text-[10px] text-ink-6">
                {activeSeries.length === 0 ? 'NO SERIES SELECTED' : 'NO WEEKS SELECTED'}
              </span>
            </div>
          ) : (
            <ChartContainer config={CHART_CONFIG} className="aspect-auto h-[420px] w-full">
              <LineChart data={rows} margin={{ top: 12, right: 16, left: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--color-ink-4)" />
                <XAxis
                  dataKey="week"
                  tickLine={false}
                  axisLine={false}
                  tickMargin={10}
                  tickFormatter={(w) => `W${w}`}
                />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  width={44}
                  domain={['auto', 'auto']}
                  tickFormatter={(v) => `${v}`}
                />
                <ChartTooltip
                  cursor={{ stroke: 'var(--color-ink-6)' }}
                  itemSorter={(item) => -(Number(item.value) || 0)}
                  content={
                    <ChartTooltipContent
                      indicator="line"
                      labelFormatter={(_, items) =>
                        `Week ${(items?.[0]?.payload as { week?: number })?.week ?? ''}`
                      }
                    />
                  }
                />
                {activeSeries.map((s) => (
                  <Line
                    key={s.key}
                    dataKey={s.key}
                    type="monotone"
                    stroke={`var(--color-${s.key})`}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeDasharray={s.dashed ? '6 4' : undefined}
                    connectNulls
                    isAnimationActive={false}
                    dot={{
                      r: 3.5,
                      strokeWidth: 2,
                      stroke: 'var(--color-card)',
                      fill: `var(--color-${s.key})`,
                    }}
                    activeDot={{ r: 5, strokeWidth: 2, stroke: 'var(--color-card)' }}
                  />
                ))}
              </LineChart>
            </ChartContainer>
          )}
          {anyPriorSeason && (
            <p className="stamp text-[9px] text-ink-6 mt-3">
              EARLY-SEASON PREDICTIONS FALL BACK TO THE PRIOR SEASON'S STATS
            </p>
          )}
        </CardContent>
      </Card>

      {/* Table view: every charted value, reachable without hovering. */}
      {rows.length > 0 && activeSeries.length > 0 && (
        <details className="mt-6">
          <summary className="cursor-pointer stamp text-[10px] text-ink-7 hover:text-ink-11 px-2 py-1 select-none">
            SHOW AS TABLE
          </summary>
          <Card className="mt-3 py-2">
            <CardContent className="px-2">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="stamp text-[10px]">WEEK</TableHead>
                    {activeSeries.map((s) => (
                      <TableHead key={s.key} className="stamp text-[10px] text-right">
                        <span className="inline-flex items-center gap-1.5">
                          <span
                            aria-hidden
                            className="h-2 w-2 rounded-[2px]"
                            style={{ background: s.color }}
                          />
                          {s.label.split(' ')[0].toUpperCase()}
                        </span>
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.week}>
                      <TableCell className="nums text-ink-9">W{row.week}</TableCell>
                      {activeSeries.map((s) => (
                        <TableCell key={s.key} className="nums text-right text-ink-11">
                          {row[s.key] !== null && row[s.key] !== undefined
                            ? (row[s.key] as number).toFixed(1)
                            : '—'}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </details>
      )}
    </main>
  )
}

function PageHeader({ season, pool, risk }: { season: number; pool: Pool; risk: number }) {
  return (
    <div className="mb-7">
      <div className="stamp text-[10px] text-ink-7 mb-2">MODEL BENCH · FULL SEASON</div>
      <h2 className="display text-[36px] text-ink-12">
        Every model, every week · <span className="text-[var(--color-signal)]">{season}</span>
      </h2>
      <p className="text-ink-8 text-sm mt-2 max-w-lg leading-relaxed">
        Weekly lineup totals for each scoring model (replayed leakage-safe at risk{' '}
        <span className="text-ink-11">{risk.toFixed(2)}</span>, pool{' '}
        <span className="text-ink-11">{pool}</span>) against what you actually fielded, with
        the hindsight-perfect lineup as the ceiling.
      </p>
    </div>
  )
}

function ProgressNote({ settled, total }: { settled: number; total: number }) {
  const pct = total > 0 ? Math.round((settled / total) * 100) : 0
  return (
    <div className="rounded-md border hairline bg-ink-2 px-4 py-3 mb-4 flex items-center gap-3 text-sm text-ink-11">
      <Loader2 size={14} className="animate-spin text-[var(--color-signal)]" />
      <span className="flex-1">
        <span className="stamp text-[10px] text-ink-7 mr-2">REPLAYING SEASON</span>
        {settled} of {total} model-week runs scored — the chart fills in as they land.
      </span>
      <span className="nums stamp text-[10px] text-ink-8">{pct}%</span>
    </div>
  )
}
