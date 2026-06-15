/**
 * Position metadata used by chips, slot stamps, eligibility filters.
 * Colors map to the --color-pos-* CSS vars in index.css.
 */

export type PositionKey =
  | 'QB' | 'RB' | 'WR' | 'TE' | 'K' | 'DEF' | 'DST'
  | 'FLEX' | 'WRRB_FLEX' | 'WRT_FLEX' | 'SUPER_FLEX'
  | 'BN' | 'IR' | 'TAXI'

export const POSITION_COLOR: Record<string, string> = {
  QB: 'var(--color-pos-qb)',
  RB: 'var(--color-pos-rb)',
  WR: 'var(--color-pos-wr)',
  TE: 'var(--color-pos-te)',
  K:  'var(--color-pos-k)',
  DEF: 'var(--color-pos-def)',
  DST: 'var(--color-pos-def)',
  FLEX: 'var(--color-pos-flex)',
  WRRB_FLEX: 'var(--color-pos-flex)',
  WRT_FLEX: 'var(--color-pos-flex)',
  SUPER_FLEX: 'var(--color-pos-flex)',
  BN: 'var(--color-pos-bn)',
  IR: 'var(--color-pos-bn)',
  TAXI: 'var(--color-pos-bn)',
}

export const SLOT_LABEL: Record<string, string> = {
  QB: 'QB',
  RB: 'RB',
  WR: 'WR',
  TE: 'TE',
  K: 'K',
  DEF: 'DEF',
  DST: 'D/ST',
  FLEX: 'FLEX',
  WRRB_FLEX: 'W/R',
  WRT_FLEX: 'W/R/T',
  SUPER_FLEX: 'SUPER',
  BN: 'BENCH',
  IR: 'IR',
  TAXI: 'TAXI',
}

export function positionColor(pos: string | null | undefined): string {
  if (!pos) return 'var(--color-pos-bn)'
  return POSITION_COLOR[pos.toUpperCase()] ?? 'var(--color-pos-bn)'
}

export function slotLabel(slot: string): string {
  return SLOT_LABEL[slot.toUpperCase()] ?? slot.toUpperCase()
}

/** Strip the numeric suffix added by the backend (RB1 -> RB, BN3 -> BN). */
export function slotBase(slotId: string): string {
  return slotId.replace(/\d+$/, '')
}
