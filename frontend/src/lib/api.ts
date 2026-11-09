/* Typed fetch helpers for the Movie Roulette API. */

export class ApiError extends Error {
  status: number;
  code: string;
  detail: Record<string, unknown>;
  constructor(status: number, code: string, message: string, detail: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "offline", "Cannot reach Movie Roulette. Is the host computer running?");
  }
  const text = await res.text();
  let data: any;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const d = (data && data.detail) || {};
    const message = typeof d === "string" ? d : d.message || `Request failed (${res.status})`;
    if (res.status === 401 && d && d.code === "host_auth_required") window.dispatchEvent(new Event("mr:host-unauthorized"));
    throw new ApiError(res.status, (d && d.code) || "error", message, typeof d === "object" ? d : {});
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b: unknown = {}) => request<T>("POST", p, b),
  patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  }
  const out = s.toString();
  return out ? `?${out}` : "";
}

export function img(path: string | null | undefined, size = "w342"): string | undefined {
  if (!path) return undefined;
  return `/api/images/${size}/${path.replace(/^\//, "")}`;
}

export function fmtTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "--:--";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export function parseTime(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  if (/^\d+(\.\d+)?$/.test(t)) return Number(t);
  const parts = t.split(":").map(Number);
  if (parts.length > 3 || parts.some((n) => Number.isNaN(n) || n < 0)) return null;
  if (parts.slice(1).some((n) => n >= 60)) return null;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

export function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/* ------------------------------------------------------------------ shared types */

export interface Standing {
  kind: "player" | "team";
  id: number;
  name: string;
  color: string;
  points: number;
  correct: number;
  rank: number;
  tied: boolean;
}

export interface Playback {
  state: "idle" | "playing" | "paused" | "ended";
  media_status: "preparing" | "ready" | "error";
  media_error: string | null;
  position: number;
  position_base: number;
  started_at: number | null;
  length: number | null;
  server_time: number;
  plays: number;
  stream_url: string | null;
}

export interface Answer {
  title: string;
  original_title: string | null;
  release_year: number | null;
  poster_path: string | null;
  backdrop_path: string | null;
  vote_average: number | null;
  genres: string[];
  directors: string[];
  cast: string[];
  overview: string | null;
  tmdb_id: number | null;
  runtime: number | null;
  collection: string | null;
  start_seconds: number | null;
}

export interface GuessView {
  id: number;
  player_id: number | null;
  player: string;
  team_id: number | null;
  verdict: "pending" | "correct" | "incorrect";
  attempt: number;
  text?: string;
  submitted_at: string;
}

export interface RoundView {
  token: string;
  number: number;
  status: "timestamp" | "ready" | "revealed" | "closed" | "skipped";
  category_label: string | null;
  difficulty: string;
  turn_player_id: number | null;
  turn_player: string | null;
  turn_team_id: number | null;
  window: { min: number; max: number } | null;
  clip_seconds: number | null;
  max_seconds: number | null;
  mode: "clip" | "continuous";
  start_seconds: number | null;
  timestamp_source: string | null;
  suggested_seconds: number | null;
  can_pick_timestamp: boolean;
  timestamp_options: { manual: boolean; random: boolean };
  hints: string[];
  hints_left: number;
  replays_left: number;
  playback: Playback | null;
  outcome: string | null;
  guesses: GuessView[];
  revealed: boolean;
  answer: Answer | null;
  host?: { movie: Answer; movie_id: number; duration: number | null; file_name: string | null; next_hint: string | null };
}

export interface GameView {
  id: number;
  name: string;
  mode: string;
  mode_label: string;
  status: "setup" | "active" | "finished" | "abandoned";
  team_mode: boolean;
  difficulty: string;
  playback_mode: string;
  clip_seconds: number;
  rounds_planned: number;
  rounds_played: number;
  filter_labels: string[];
  logic: string;
  players: { id: number; name: string; color: string; team_id: number | null; team: string | null; online: boolean }[];
  teams: { id: number; name: string; color: string }[];
  scoreboard: { players: Standing[]; teams: Standing[] };
  round: RoundView | null;
  history: {
    number: number;
    status: string;
    outcome: string | null;
    start_seconds: number | null;
    winners: number[];
    title?: string;
    release_year?: number | null;
    poster_path?: string | null;
  }[];
  started_at: string | null;
  finished_at: string | null;
  video_on_player_devices: boolean;
  guess_mode: string;
  join_code?: string | null;
  config?: Record<string, any>;
}

export interface MovieCard {
  id: number;
  title: string;
  release_year: number | null;
  poster_path: string | null;
  vote_average: number | null;
  metadata_status: "unmatched" | "needs_review" | "matched" | "failed";
  match_confidence: number | null;
  excluded_from_game: boolean;
  playable: boolean;
  duration: number | null;
  file_statuses: string[];
  file_count: number;
  parsed_title: string | null;
  parsed_year: number | null;
  genres: string[];
}

export interface Category {
  id: number;
  kind: "genre" | "decade" | "collection" | "studio" | "custom";
  name: string;
  ref_id: number | null;
  rules: Record<string, any>;
  description: string | null;
  hidden: boolean;
  sort_order: number;
  count: number;
}

export interface Player {
  id: number;
  name: string;
  color: string;
  team_id: number | null;
  team: string | null;
  archived: boolean;
}

export interface Team {
  id: number;
  name: string;
  color: string;
  max_size: number | null;
  members: { id: number; name: string; color: string }[];
}

export interface Facets {
  genres: { id: number; name: string; count: number }[];
  decades: { id: number; name: string; count: number }[];
  collections: { id: number; name: string; count: number }[];
  studios: { id: number; name: string; count: number }[];
  status_counts: Record<string, number>;
}
