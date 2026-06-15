import { useState } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { Heart, HeartCrack, Settings } from 'lucide-react'
import { cn } from '@/lib/cn'
import { FieldSelect } from './FieldSelect'

/**
 * Compact manager-preferences popover for prefer_team / avoid_team.
 * ±10% multiplier on the score for any player on the chosen team —
 * lets the user nudge the model toward gut-feel team plays.
 */

const TEAMS = [
  'ARI','ATL','BAL','BUF','CAR','CHI','CIN','CLE','DAL','DEN','DET','GB','HOU','IND','JAX','KC',
  'LAC','LAR','LV','MIA','MIN','NE','NO','NYG','NYJ','PHI','PIT','SEA','SF','TB','TEN','WAS',
]

export function TeamPrefs({
  prefer,
  avoid,
  onChange,
}: {
  prefer: string | null
  avoid: string | null
  onChange: (p: { prefer: string | null; avoid: string | null }) => void
}) {
  const [open, setOpen] = useState(false)
  const has = prefer || avoid

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          aria-label="Team preferences"
          className={cn(
            'grid place-items-center w-8 h-8 rounded-md border hairline transition',
            has
              ? 'bg-[var(--color-signal)]/10 border-[var(--color-signal)]/40 text-[var(--color-signal)]'
              : 'bg-ink-2 hover:bg-ink-3 text-ink-9 hover:text-ink-12',
          )}
        >
          <Settings size={14} />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={8}
          className="bg-ink-1 border hairline rounded-md shadow-xl p-5 w-72 z-50 pop-in"
        >
          <div className="stamp text-[10px] text-ink-7 mb-1">MANAGER PREFERENCES</div>
          <p className="text-xs text-ink-8 mb-5 leading-relaxed">
            Nudge the model ±10% on scores for picks on these teams.
          </p>

          <div className="mb-4">
            <label className="flex items-center gap-2 stamp text-[10px] text-[var(--color-good)] mb-2">
              <Heart size={11} strokeWidth={2.5} />
              PREFER
            </label>
            <TeamSelect
              value={prefer}
              onChange={(t) => onChange({ prefer: t, avoid })}
            />
          </div>

          <div>
            <label className="flex items-center gap-2 stamp text-[10px] text-[var(--color-signal)] mb-2">
              <HeartCrack size={11} strokeWidth={2.5} />
              AVOID
            </label>
            <TeamSelect
              value={avoid}
              onChange={(t) => onChange({ prefer, avoid: t })}
            />
          </div>

          {has && (
            <button
              onClick={() => onChange({ prefer: null, avoid: null })}
              className="mt-5 stamp text-[10px] text-ink-7 hover:text-ink-11 transition"
            >
              CLEAR ALL
            </button>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

function TeamSelect({
  value,
  onChange,
}: {
  value: string | null
  onChange: (t: string | null) => void
}) {
  return (
    <FieldSelect
      value={value ?? '__none'}
      onChange={(v) => onChange(v === '__none' ? null : v)}
      options={[{ value: '__none', label: '— none —' }, ...TEAMS.map((t) => ({ value: t, label: t }))]}
      size="sm"
    />
  )
}
