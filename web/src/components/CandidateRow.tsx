import { Check, ChevronRight, Eye, ShieldCheck } from 'lucide-react'
import { cn } from '@/lib/cn'
import { positionColor } from '@/lib/positions'
import type { Candidate } from '@/lib/api'
import { PlayerAvatar } from './PlayerAvatar'

/**
 * One ranked candidate in the recommendation list.
 *
 * Rank #1 (recommended=true) gets the BIG treatment — ESPN-red signal
 * glow, "RECOMMENDED" stamp, larger headshot, prominent score number.
 * The rest are compact rows.
 *
 * Two actions:
 * - Use (pins this player to the active slot)
 * - View (opens the player detail drawer)
 */
export function CandidateRow({
  candidate,
  pinned,
  onUse,
  onView,
  index = 0,
}: {
  candidate: Candidate
  pinned: boolean
  onUse: () => void
  onView: () => void
  index?: number
}) {
  const { player, score, recommended, rank } = candidate
  const color = positionColor(player.position)

  if (recommended) {
    return (
      <div
        className={cn(
          'group relative w-full rise-in',
          'rounded-lg overflow-hidden bg-ink-2 border border-[var(--color-signal)]/40',
          'signal-glow p-5',
        )}
        style={{ animationDelay: `${index * 60}ms` }}
      >
        <div className="absolute top-0 left-0 flex items-center gap-1.5 bg-[var(--color-signal)] text-white px-2.5 py-1 rounded-br-md">
          <ShieldCheck size={11} strokeWidth={2.5} />
          <span className="stamp text-[9px] tracking-[0.22em]">RECOMMENDED · #{rank}</span>
        </div>

        <div className="flex items-stretch gap-5 pt-3">
          <div className="shrink-0">
            <PlayerAvatar player={player} size={64} />
          </div>

          <div className="flex-1 min-w-0">
            <div className="display text-2xl text-ink-12 truncate">{player.full_name}</div>
            <div className="flex items-center gap-2 mt-1.5">
              <span
                className="stamp text-[10px]"
                style={{ color: `color-mix(in oklch, ${color} 80%, white)` }}
              >
                {player.position}
              </span>
              <span className="text-ink-7 text-[10px]">·</span>
              <span className="stamp text-[10px] text-ink-9">{player.team ?? '—'}</span>
              {score.on_user_roster && (
                <>
                  <span className="text-ink-7 text-[10px]">·</span>
                  <span className="stamp text-[10px] text-ink-9">ON ROSTER</span>
                </>
              )}
            </div>

            <div className="grid grid-cols-3 gap-6 mt-4">
              <Metric value={score.final_score.toFixed(2)} label="SCORE" />
              <Metric value={score.projected_mean.toFixed(2)} label="PROJ MEAN" muted />
              <Metric value={`±${score.projected_variance.toFixed(2)}`} label="VARIANCE" muted />
            </div>

            <div className="mt-4">
              <ConfidencePill confidence={score.confidence} />
            </div>
          </div>
        </div>

        <div className="mt-5 pt-4 border-t hairline flex items-center gap-2">
          <ActionButton primary onClick={onUse} active={pinned}>
            {pinned ? (
              <>
                <Check size={11} strokeWidth={2.5} />
                PINNED
              </>
            ) : (
              'USE THIS PLAYER'
            )}
          </ActionButton>
          <ActionButton onClick={onView}>
            <Eye size={11} />
            WEEKLY STATS
          </ActionButton>
        </div>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'group w-full rise-in',
        'flex items-center gap-3 px-4 py-3 rounded-md',
        'bg-ink-2 border hairline transition hover:bg-ink-3',
        pinned && 'ring-1 ring-[var(--color-good)]/40 bg-ink-3',
      )}
      style={{ animationDelay: `${index * 50}ms` }}
    >
      <span className="stamp text-[11px] text-ink-7 w-6 nums shrink-0">#{rank}</span>
      <PlayerAvatar player={player} size={36} />
      <div className="flex-1 min-w-0">
        <div className="text-[14px] text-ink-12 font-medium truncate">{player.full_name}</div>
        <div className="flex items-center gap-1.5 mt-0.5">
          <span
            className="stamp text-[9px]"
            style={{ color: `color-mix(in oklch, ${color} 75%, white)` }}
          >
            {player.position}
          </span>
          <span className="text-ink-7 text-[9px]">·</span>
          <span className="stamp text-[9px] text-ink-8">{player.team ?? '—'}</span>
          {score.on_user_roster && (
            <>
              <span className="text-ink-7 text-[9px]">·</span>
              <span className="stamp text-[9px] text-ink-8">ROSTER</span>
            </>
          )}
          {pinned && (
            <>
              <span className="text-ink-7 text-[9px]">·</span>
              <span className="stamp text-[9px] text-[var(--color-good)]">PINNED</span>
            </>
          )}
        </div>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <div className="flex flex-col items-end">
          <span className="nums text-base text-ink-11 font-semibold leading-none">
            {score.final_score.toFixed(2)}
          </span>
          <span className="stamp text-[8px] text-ink-7 mt-1">SCORE</span>
        </div>
        <ConfidenceDot confidence={score.confidence} />
        <ActionButton small primary={!pinned} active={pinned} onClick={onUse}>
          {pinned ? (
            <>
              <Check size={10} strokeWidth={2.5} />
              PINNED
            </>
          ) : (
            'USE'
          )}
        </ActionButton>
        <button
          onClick={onView}
          aria-label="View weekly stats"
          className="grid place-items-center w-7 h-7 rounded-sm text-ink-7 hover:text-ink-12 hover:bg-ink-4 transition"
        >
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  )
}

function Metric({ value, label, muted = false }: { value: string; label: string; muted?: boolean }) {
  return (
    <div className="flex flex-col">
      <span
        className={cn(
          'nums text-2xl font-semibold leading-none',
          muted ? 'text-ink-10' : 'text-ink-12',
        )}
      >
        {value}
      </span>
      <span className="stamp text-[9px] text-ink-7 mt-1.5">{label}</span>
    </div>
  )
}

function ActionButton({
  children,
  onClick,
  primary = false,
  active = false,
  small = false,
}: {
  children: React.ReactNode
  onClick: () => void
  primary?: boolean
  active?: boolean
  small?: boolean
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'stamp inline-flex items-center justify-center gap-1.5 rounded-md transition cursor-pointer',
        small ? 'h-7 px-2.5 text-[9px]' : 'h-9 px-3.5 text-[10px]',
        primary && !active &&
          'bg-[var(--color-signal)] text-white hover:brightness-110',
        primary && active &&
          'bg-[var(--color-good)] text-white',
        !primary &&
          'bg-ink-3 text-ink-11 hover:bg-ink-4',
      )}
    >
      {children}
    </button>
  )
}

function ConfidencePill({ confidence }: { confidence: 'low' | 'medium' | 'high' }) {
  const map = {
    high:   { c: 'var(--color-conf-high)',   label: 'HIGH CONFIDENCE' },
    medium: { c: 'var(--color-conf-medium)', label: 'MEDIUM CONFIDENCE' },
    low:    { c: 'var(--color-conf-low)',    label: 'LOW CONFIDENCE' },
  }[confidence]
  return (
    <span
      className="stamp text-[9px] inline-flex items-center gap-1.5 px-2 py-1 rounded-xs"
      style={{
        background: `color-mix(in oklch, ${map.c} 14%, transparent)`,
        color: `color-mix(in oklch, ${map.c} 85%, white)`,
        boxShadow: `inset 0 0 0 1px color-mix(in oklch, ${map.c} 30%, transparent)`,
      }}
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: map.c }} />
      {map.label}
    </span>
  )
}

function ConfidenceDot({ confidence }: { confidence: 'low' | 'medium' | 'high' }) {
  const c = {
    high:   'var(--color-conf-high)',
    medium: 'var(--color-conf-medium)',
    low:    'var(--color-conf-low)',
  }[confidence]
  return (
    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: c }} title={`${confidence} confidence`} />
  )
}
