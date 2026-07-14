/**
 * Typed API client mirroring services/api/src/api/schemas.py.
 *
 * Runtime-validates every response with Zod so a backend schema drift
 * surfaces as a clean error toast instead of a silent UI bug.
 *
 * Goes through Vite's /api proxy in dev, so no CORS dance.
 */

import { z } from 'zod'

const BASE = '/api'

/* === Wire schemas — keep in lockstep with services/api/.../schemas.py === */

export const PlayerSchema = z.object({
  player_id: z.string(),
  full_name: z.string().nullable(),
  position: z.string().nullable(),
  fantasy_positions: z.array(z.string()),
  team: z.string().nullable(),
  status: z.string().nullable(),
  injury_status: z.string().nullable(),
  headshot_url: z.string().nullable(),
})
export type Player = z.infer<typeof PlayerSchema>

export const StateSchema = z.object({
  season: z.number(),
  week: z.number(),
})
export type AppState = z.infer<typeof StateSchema>

export const LeagueSummarySchema = z.object({
  league_id: z.string(),
  name: z.string(),
  season: z.string(),
})
export type LeagueSummary = z.infer<typeof LeagueSummarySchema>

export const UserLeaguesSchema = z.object({
  user_id: z.string(),
  username: z.string().nullable(),
  display_name: z.string().nullable(),
  leagues: z.array(LeagueSummarySchema),
})
export type UserLeagues = z.infer<typeof UserLeaguesSchema>

export const RosterSlotSchema = z.object({
  slot_id: z.string(),
  slot: z.string(),
  selectable: z.boolean(),
  starter_player: PlayerSchema.nullable(),
})
export type RosterSlot = z.infer<typeof RosterSlotSchema>

export const LeagueContextSchema = z.object({
  league: LeagueSummarySchema,
  user_id: z.string(),
  username: z.string().nullable(),
  display_name: z.string().nullable(),
  roster_positions: z.array(z.string()),
  slots: z.array(RosterSlotSchema),
  bench: z.array(PlayerSchema),
  all_roster_players: z.array(PlayerSchema),
})
export type LeagueContext = z.infer<typeof LeagueContextSchema>

const ConfidenceSchema = z.enum(['low', 'medium', 'high'])
const PoolSchema = z.enum(['roster', 'waivers', 'both'])
export type Pool = z.infer<typeof PoolSchema>
export type Confidence = z.infer<typeof ConfidenceSchema>

/** Registered scoring models (decision-engine core/scoring MODELS). */
export const MODELS = [
  { value: 'context', label: 'Context (regression)' },
  { value: 'naive', label: 'Naive baseline' },
] as const
export type Model = (typeof MODELS)[number]['value']

export const ScoreSchema = z.object({
  projected_mean: z.number(),
  projected_variance: z.number(),
  risk_adjusted_score: z.number(),
  final_score: z.number(),
  confidence: ConfidenceSchema,
  notes: z.array(z.string()),
  preference_note: z.string().nullable(),
  on_user_roster: z.boolean(),
})
export type Score = z.infer<typeof ScoreSchema>

export const CandidateSchema = z.object({
  rank: z.number(),
  recommended: z.boolean(),
  player: PlayerSchema,
  score: ScoreSchema,
})
export type Candidate = z.infer<typeof CandidateSchema>

export const DecideSchema = z.object({
  season: z.number(),
  week: z.number(),
  slot: z.string(),
  pool: PoolSchema,
  risk: z.number(),
  candidates: z.array(CandidateSchema),
})
export type Decide = z.infer<typeof DecideSchema>

export const SlotDecisionSchema = z.object({
  slot_id: z.string(),
  slot: z.string(),
  recommended: CandidateSchema.nullable(),
  current_starter: PlayerSchema.nullable(),
  matches_current: z.boolean(),
  // Optional so the UI keeps working against a backend that predates it.
  current_starter_score: ScoreSchema.nullable().optional(),
})
export type SlotDecision = z.infer<typeof SlotDecisionSchema>

export const DecisionsSchema = z.object({
  season: z.number(),
  week: z.number(),
  risk: z.number(),
  pool: PoolSchema,
  decisions: z.array(SlotDecisionSchema),
  projection_total: z.number(),
  projection_variance_total: z.number(),
  projection_stddev_total: z.number(),
  using_prior_season: z.boolean(),
  prior_season: z.number().nullable(),
})
export type Decisions = z.infer<typeof DecisionsSchema>

export const ComparisonPlayerSchema = z.object({
  player: PlayerSchema,
  // null = the model never scored them (no slot accepts the position)
  predicted_mean: z.number().nullable(),
  // null = no stat row that week, i.e. they didn't play
  actual_points: z.number().nullable(),
})
export type ComparisonPlayer = z.infer<typeof ComparisonPlayerSchema>

export const ComparisonSlotSchema = z.object({
  slot_id: z.string(),
  slot: z.string(),
  model_pick: ComparisonPlayerSchema.nullable(),
  actual_starter: ComparisonPlayerSchema.nullable(),
  same_player: z.boolean(),
})
export type ComparisonSlot = z.infer<typeof ComparisonSlotSchema>

export const ComparisonSchema = z.object({
  season: z.number(),
  week: z.number(),
  model: z.string(),
  risk: z.number(),
  pool: PoolSchema,
  slots: z.array(ComparisonSlotSchema),
  totals: z.object({
    model_predicted: z.number(),
    model_actual: z.number(),
    human_predicted: z.number().nullable(),
    human_actual: z.number(),
    perfect_actual: z.number().nullable(),
  }),
  accuracy: z.object({
    n: z.number(),
    mae: z.number().nullable(),
    // signed, predicted - actual: positive = the model over-projected
    mean_error: z.number().nullable(),
  }),
  roster: z.array(ComparisonPlayerSchema),
  using_prior_season: z.boolean(),
  prior_season: z.number().nullable(),
})
export type Comparison = z.infer<typeof ComparisonSchema>

export const WeeklyStatLineSchema = z.object({
  week: z.number(),
  points: z.number(),
  stats: z.record(z.string(), z.number()),
})
export type WeeklyStatLine = z.infer<typeof WeeklyStatLineSchema>

export const PlayerStatsSchema = z.object({
  player: PlayerSchema,
  season: z.number(),
  weeks: z.array(WeeklyStatLineSchema),
  season_total_points: z.number(),
  games_played: z.number(),
  points_per_game: z.number(),
  mean: z.number(),
  stddev: z.number(),
  using_prior_season: z.boolean(),
  prior_season: z.number().nullable(),
})
export type PlayerStats = z.infer<typeof PlayerStatsSchema>

/* === Transport === */

class ApiError extends Error {
  status: number
  body: string
  detail?: string
  constructor(status: number, body: string, detail?: string) {
    super(detail ?? body)
    this.name = 'ApiError'
    this.status = status
    this.body = body
    this.detail = detail
  }
}

async function request<T extends z.ZodTypeAny>(
  schema: T,
  path: string,
  params?: Record<string, string | number | undefined | null>,
): Promise<z.infer<T>> {
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    }
  }
  const res = await fetch(url.toString())
  const body = await res.text()
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = JSON.parse(body)?.error ?? JSON.parse(body)?.detail
    } catch { /* fall through with raw body */ }
    throw new ApiError(res.status, body, detail)
  }
  const parsed = schema.safeParse(JSON.parse(body))
  if (!parsed.success) {
    console.error('Schema mismatch on', path, parsed.error)
    throw new ApiError(500, body, 'response schema mismatch — backend drifted')
  }
  return parsed.data
}

export { ApiError }

export const api = {
  state: () => request(StateSchema, '/state'),
  userLeagues: (username: string, season?: number) =>
    request(UserLeaguesSchema, `/users/${encodeURIComponent(username)}/leagues`, { season }),
  context: (leagueId: string, user: string, season?: number) =>
    request(LeagueContextSchema, `/leagues/${leagueId}/context`, { user, season }),
  decide: (args: {
    user: string
    league_id: string
    slot: string
    risk?: number
    pool?: Pool
    limit?: number
    model?: Model
    season?: number
    week?: number
    prefer_team?: string
    avoid_team?: string
  }) => request(DecideSchema, '/decide', args),
  decisions: (args: {
    league_id: string
    user: string
    risk?: number
    pool?: Pool
    model?: Model
    season?: number
    week?: number
    prefer_team?: string
    avoid_team?: string
  }) => {
    const { league_id, ...rest } = args
    return request(DecisionsSchema, `/leagues/${league_id}/decisions`, rest)
  },
  comparison: (args: {
    league_id: string
    user: string
    risk?: number
    pool?: Pool
    model?: Model
    season?: number
    week?: number
  }) => {
    const { league_id, ...rest } = args
    return request(ComparisonSchema, `/leagues/${league_id}/comparison`, rest)
  },
  playerStats: (playerId: string, leagueId: string, season?: number, week?: number) =>
    request(PlayerStatsSchema, `/players/${playerId}/stats`, {
      league_id: leagueId,
      season,
      week,
    }),
}
