import { ArrowRightLeft, Check, ChevronRight, Pin, X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { positionColor, slotBase, slotLabel } from '@/lib/positions'
import type { Player } from '@/lib/api'

/**
 * One row in the lineup. Shows whichever player is "filling" this slot
 * right now — pinned (user-overridden) > model recommendation > current
 * Sleeper starter — and a status badge that explains why.
 */
export function SlotCard({
  decision,
  active,
  pinned,
  onClick,
  onClearPin,
  index = 0,
}: {
  decision: {
    slot_id: string
    slot: string
    /** What the model picks for this slot */
    recommended_player: Player | null
    /** What Sleeper has assigned right now */
    current_player: Player | null
    /** Recommended matches current Sleeper starter */
    matches: boolean
    score: number | null
    variance: number | null
    confidence: 'low' | 'medium' | 'high' | null
    /** Projected points gained by swapping to the recommendation (null when match/unknown). */
    swapGain: number | null
  }
  /** A player the user has explicitly chosen for this slot (overrides recommendation). */
  pinned: { player: Player; score: number } | null
  active?: boolean
  onClick?: () => void
  onClearPin?: () => void
  index?: number
}) {
  const base = slotBase(decision.slot_id)
  const color = positionColor(base)

  // Which player do we actually show?
  const shown = pinned?.player ?? decision.recommended_player ?? decision.current_player
  const shownScore = pinned?.score ?? decision.score

  // Status badge logic
  let status: 'pinned' | 'match' | 'swap' | 'none' = 'none'
  if (pinned) status = 'pinned'
  else if (decision.recommended_player && decision.matches) status = 'match'
  else if (decision.recommended_player && !decision.matches) status = 'swap'

  return (
    <div
      className={cn(
        'group relative w-full rise-in',
        'flex items-stretch gap-0 overflow-hidden',
        'bg-ink-2 border hairline rounded-md',
        'transition-all duration-200',
        'hover:bg-ink-3 hover:-translate-y-px hover:shadow-lg',
        active && 'bg-ink-3 ring-1 ring-[color-mix(in_oklch,var(--color-signal)_50%,transparent)] shadow-[0_8px_32px_-12px_var(--color-signal-glow)]',
        pinned && 'ring-1 ring-[color-mix(in_oklch,var(--color-good)_45%,transparent)]',
      )}
      style={{ animationDelay: `${index * 28}ms` }}
    >
      {/* Stamped position tab */}
      <div
        className="flex flex-col items-center justify-center px-3 py-3 border-r hairline shrink-0"
        style={{
          background: `linear-gradient(180deg, color-mix(in oklch, ${color} 14%, transparent), color-mix(in oklch, ${color} 6%, transparent))`,
          minWidth: '60px',
        }}
      >
        <span
          className="stamp text-[11px] font-semibold tracking-[0.20em]"
          style={{ color: `color-mix(in oklch, ${color} 90%, white)` }}
        >
          {slotLabel(base)}
        </span>
      </div>

      <button
        type="button"
        onClick={onClick}
        className={cn(
          'flex items-center gap-3 flex-1 px-4 py-3 min-w-0 cursor-pointer text-left',
        )}
      >
        <PlayerAvatarInline player={shown} color={color} />

        <div className="flex-1 min-w-0">
          {shown ? (
            <>
              <div className="text-[15px] text-ink-12 font-medium truncate leading-tight">
                {shown.full_name}
              </div>
              <div className="flex items-center gap-1.5 mt-1">
                {shown.position && (
                  <span
                    className="stamp text-[9px]"
                    style={{ color: `color-mix(in oklch, ${positionColor(shown.position)} 75%, white)` }}
                  >
                    {shown.position}
                  </span>
                )}
                <span className="text-ink-7 text-[10px]">·</span>
                <span className="stamp text-[10px] text-ink-8">{shown.team ?? '—'}</span>
                {shown.injury_status && (
                  <>
                    <span className="text-ink-7 text-[10px]">·</span>
                    <span className="stamp text-[10px] text-[var(--color-signal)]">
                      {shown.injury_status}
                    </span>
                  </>
                )}
                {status === 'pinned' && <PinnedBadge />}
                {status === 'match' && <MatchBadge />}
                {status === 'swap' && (
                  <SwapBadge
                    currentName={decision.current_player?.full_name ?? null}
                    gain={decision.swapGain}
                  />
                )}
              </div>
            </>
          ) : (
            <div className="text-[14px] text-ink-7 italic">empty slot</div>
          )}
        </div>

        {shownScore !== null && (
          <div className="flex flex-col items-end leading-none shrink-0">
            <span className="nums text-base text-ink-12 font-semibold">
              {shownScore.toFixed(2)}
            </span>
            <span className="stamp text-[8px] text-ink-7 mt-1">PROJ</span>
          </div>
        )}

        <ChevronRight
          size={14}
          className={cn(
            'shrink-0 transition-transform',
            active
              ? 'text-[var(--color-signal)] translate-x-0.5'
              : 'text-ink-7 group-hover:translate-x-0.5 group-hover:text-ink-11',
          )}
        />
      </button>

      {pinned && onClearPin && (
        <button
          onClick={(e) => {
            e.stopPropagation()
            onClearPin()
          }}
          aria-label="Clear pin"
          title="Revert to recommended"
          className="grid place-items-center w-9 mr-1 my-1 rounded-sm text-ink-7 hover:text-ink-12 hover:bg-ink-4 transition cursor-pointer"
        >
          <X size={12} />
        </button>
      )}
    </div>
  )
}

function PlayerAvatarInline({
  player,
  color,
}: {
  player: Player | null | undefined
  color: string
}) {
  if (!player) {
    return (
      <div
        className="shrink-0 rounded-full bg-ink-3 border hairline grid place-items-center"
        style={{ width: 40, height: 40 }}
      >
        <span className="stamp text-[10px] text-ink-7">—</span>
      </div>
    )
  }
  if (player.headshot_url) {
    return (
      <div
        className="shrink-0 rounded-full overflow-hidden bg-ink-3 border hairline"
        style={{
          width: 40,
          height: 40,
          boxShadow: `inset 0 0 0 1px color-mix(in oklch, ${color} 30%, transparent)`,
        }}
      >
        <img
          src={player.headshot_url}
          alt={player.full_name ?? ''}
          loading="lazy"
          decoding="async"
          className="w-full h-full object-cover"
        />
      </div>
    )
  }
  const initials = (player.full_name ?? '?').split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase()
  return (
    <div
      className="shrink-0 rounded-full grid place-items-center font-semibold text-[12px] border hairline"
      style={{
        width: 40,
        height: 40,
        background: `color-mix(in oklch, ${color} 22%, var(--color-ink-3))`,
        color: `color-mix(in oklch, ${color} 80%, white)`,
        boxShadow: `inset 0 0 0 1px color-mix(in oklch, ${color} 35%, transparent)`,
      }}
    >
      {initials}
    </div>
  )
}

function MatchBadge() {
  return (
    <span
      className="stamp text-[8px] inline-flex items-center gap-1 px-1.5 py-0.5 rounded-xs ml-1"
      style={{
        background: 'color-mix(in oklch, var(--color-good) 14%, transparent)',
        color: 'color-mix(in oklch, var(--color-good) 85%, white)',
        boxShadow: 'inset 0 0 0 1px color-mix(in oklch, var(--color-good) 30%, transparent)',
      }}
    >
      <Check size={9} strokeWidth={3} />
      MATCH
    </span>
  )
}

function SwapBadge({
  currentName,
  gain,
}: {
  currentName: string | null
  gain: number | null
}) {
  const title = [
    currentName ? `Currently starting ${currentName}` : null,
    gain !== null ? `swapping projects ${gain >= 0 ? '+' : ''}${gain.toFixed(1)} pts` : null,
  ]
    .filter(Boolean)
    .join(' — ')
  return (
    <span
      className="stamp text-[8px] inline-flex items-center gap-1 px-1.5 py-0.5 rounded-xs ml-1"
      style={{
        background: 'color-mix(in oklch, var(--color-signal) 14%, transparent)',
        color: 'color-mix(in oklch, var(--color-signal) 85%, white)',
        boxShadow: 'inset 0 0 0 1px color-mix(in oklch, var(--color-signal) 30%, transparent)',
      }}
      title={title || undefined}
    >
      <ArrowRightLeft size={9} strokeWidth={2.5} />
      SWAP
      {gain !== null && (
        <span className="nums font-semibold">
          {gain >= 0 ? '+' : ''}{gain.toFixed(1)}
        </span>
      )}
    </span>
  )
}

function PinnedBadge() {
  return (
    <span
      className="stamp text-[8px] inline-flex items-center gap-1 px-1.5 py-0.5 rounded-xs ml-1"
      style={{
        background: 'color-mix(in oklch, var(--color-good) 18%, transparent)',
        color: 'color-mix(in oklch, var(--color-good) 88%, white)',
        boxShadow: 'inset 0 0 0 1px color-mix(in oklch, var(--color-good) 38%, transparent)',
      }}
    >
      <Pin size={9} strokeWidth={2.5} />
      PINNED
    </span>
  )
}
