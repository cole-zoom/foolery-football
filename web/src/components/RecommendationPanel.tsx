import { useQuery } from '@tanstack/react-query'
import { Loader2, X } from 'lucide-react'
import { api, type Candidate, type Model, type Pool } from '@/lib/api'
import { cn } from '@/lib/cn'
import { slotBase, slotLabel } from '@/lib/positions'
import { FieldSelect } from './FieldSelect'
import { Sheet } from './Sheet'
import { CandidateRow } from './CandidateRow'

/**
 * Right-side recommendation drawer. Drives /decide off slot + risk + pool.
 */
export function RecommendationPanel({
  open,
  onOpenChange,
  user,
  leagueId,
  season,
  week,
  slotId,
  risk,
  pool,
  model,
  onPoolChange,
  pinnedPlayerId,
  onPin,
  onUnpin,
  onViewPlayer,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  user: string
  leagueId: string
  season: number
  week: number | null
  slotId: string | null
  risk: number
  pool: Pool
  model: Model
  onPoolChange: (p: Pool) => void
  pinnedPlayerId: string | null
  onPin: (c: Candidate) => void
  onUnpin: () => void
  onViewPlayer: (playerId: string) => void
}) {
  const slotBaseStr = slotId ? slotBase(slotId) : null

  const q = useQuery({
    enabled: open && !!slotBaseStr && week !== null,
    queryKey: ['decide', user, leagueId, season, week, slotBaseStr, risk, pool, model],
    queryFn: () =>
      api.decide({
        user,
        league_id: leagueId,
        slot: slotBaseStr!,
        risk,
        pool,
        model,
        season,
        week: week ?? undefined,
        limit: 12,
      }),
    placeholderData: (prev) => prev,
  })

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      subtitle={
        <>
          DECISION ·{' '}
          {slotId && (
            <span className="text-[var(--color-signal)]">{slotLabel(slotBaseStr ?? '')}</span>
          )}
        </>
      }
      title={
        <span>
          Who starts at{' '}
          <span className="text-[var(--color-signal)]">
            {slotBaseStr && slotLabel(slotBaseStr)}
          </span>
          ?
        </span>
      }
      width="lg"
    >
      <div className="px-7 py-4 border-b hairline flex items-center justify-between gap-4">
        <FieldSelect
          label="POOL"
          value={pool}
          onChange={(v) => onPoolChange(v as Pool)}
          options={[
            { value: 'roster', label: 'My roster' },
            { value: 'both', label: 'Roster + waivers' },
            { value: 'waivers', label: 'Waivers only' },
          ]}
          size="sm"
          triggerClassName="min-w-[160px]"
        />

        {q.isFetching && (
          <div className="flex items-center gap-2 text-ink-8">
            <Loader2 size={12} className="animate-spin" />
            <span className="stamp text-[10px]">SCORING</span>
          </div>
        )}
      </div>

      {pinnedPlayerId && (
        <div className="px-7 py-3 border-b hairline bg-[var(--color-good)]/6 flex items-center justify-between">
          <div className="flex items-center gap-2 text-[12px] text-ink-11">
            <span className="stamp text-[10px] text-[var(--color-good)]">PINNED</span>
            <span className="text-ink-9">
              You've overridden the model for this slot.
            </span>
          </div>
          <button
            onClick={onUnpin}
            className="stamp text-[10px] inline-flex items-center gap-1 text-ink-7 hover:text-ink-12 transition"
          >
            <X size={11} />
            REVERT TO RECOMMENDED
          </button>
        </div>
      )}

      <div className="px-7 py-6 space-y-3">
        {q.isLoading && !q.data && (
          <>
            <SkeletonRow tall />
            <SkeletonRow />
            <SkeletonRow />
            <SkeletonRow />
          </>
        )}

        {q.error && (
          <div className="rounded-md border border-[var(--color-signal)]/40 bg-[var(--color-signal)]/8 px-4 py-3 text-sm text-ink-11">
            <span className="stamp text-[10px] text-[var(--color-signal)] mr-2">ERROR</span>
            {(q.error as Error).message}
          </div>
        )}

        {q.data?.candidates.length === 0 && !q.isLoading && (
          <div className="text-ink-8 text-sm py-12 text-center">
            <span className="stamp text-[10px] block mb-1 text-ink-7">NO CANDIDATES</span>
            Nothing in the {pool} pool is eligible for this slot.
          </div>
        )}

        {q.data?.candidates.map((c, i) => (
          <CandidateRow
            key={c.player.player_id}
            candidate={c}
            index={i}
            pinned={pinnedPlayerId === c.player.player_id}
            onUse={() => onPin(c)}
            onView={() => onViewPlayer(c.player.player_id)}
          />
        ))}
      </div>

      {q.data && (
        <div className="px-7 py-4 border-t hairline flex items-center justify-between stamp text-[10px] text-ink-7">
          <span>SCORED {q.data.candidates.length} CANDIDATES</span>
          <span>
            WK {q.data.week} · {q.data.season} · RISK {q.data.risk.toFixed(2)} ·{' '}
            {model.toUpperCase()}
          </span>
        </div>
      )}
    </Sheet>
  )
}

function SkeletonRow({ tall = false }: { tall?: boolean }) {
  return (
    <div
      className={cn(
        'animate-pulse bg-ink-2 border hairline rounded-md',
        tall ? 'h-40' : 'h-16',
      )}
    />
  )
}
