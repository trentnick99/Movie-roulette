import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Check, X } from "lucide-react";
import { fmtTime, qs, type GameView, type Player } from "../lib/api";
import { useApi } from "../lib/hooks";
import { Empty, ErrorNote, PageHeader, Poster, Spinner } from "../components/ui";

interface GameRow {
  id: number;
  name: string;
  mode_label: string;
  mode: string;
  status: string;
  created_at: string;
  rounds: number;
  players: string[];
  leaders: { name: string; points: number; rank: number }[];
  difficulty: string;
}

export default function HistoryPage() {
  const players = useApi<Player[]>("/api/players?include_archived=true");
  const [status, setStatus] = useState("");
  const [player, setPlayer] = useState("");
  const [mode, setMode] = useState("");
  const [q, setQ] = useState("");
  const games = useApi<GameRow[]>(`/api/games${qs({ status, player_id: player, mode, q })}`);
  return (
    <div className="max-w-5xl">
      <PageHeader title="Game history" />
      <div className="flex flex-wrap gap-2 mb-5">
        <input className="field w-56" placeholder="Search game names" aria-label="Search game names" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="field w-auto" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All games</option>
          <option value="finished">Finished</option>
          <option value="active">In progress</option>
          <option value="abandoned">Abandoned</option>
        </select>
        <select className="field w-auto" aria-label="Player" value={player} onChange={(e) => setPlayer(e.target.value)}>
          <option value="">Any player</option>
          {players.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select className="field w-auto" aria-label="Mode" value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="">Any mode</option>
          {[["category", "Category Roulette"], ["library", "Full Library"], ["franchise", "Franchise"], ["decade", "Decade"], ["person", "Director or Actor"], ["custom", "Custom"], ["difficulty", "Difficulty"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      {!games.data ? <Spinner /> : games.data.length === 0 ? (
        <Empty title="No games yet" action={<Link className="btn btn-lamp" to="/play">Start a game</Link>} />
      ) : (
        <ul className="surface divide-y divide-line/60">
          {games.data.map((g) => (
            <li key={g.id}>
              <Link to={g.status === "active" || g.status === "setup" ? `/game/${g.id}` : `/history/${g.id}`} className="grid sm:grid-cols-[1fr_auto] gap-1 px-4 py-3 hover:bg-panel-2">
                <span>
                  <span className="block">{g.name}</span>
                  <span className="text-xs text-dim">
                    {new Date(g.created_at).toLocaleDateString()}, {g.mode_label}, {g.rounds} round{g.rounds === 1 ? "" : "s"}, {g.players.join(", ")}
                  </span>
                </span>
                <span className="text-sm text-dim self-center">
                  {g.status === "finished" ? (g.leaders[0] && g.leaders[0].points > 0 ? `Won by ${g.leaders.filter((l) => l.rank === 1).map((l) => l.name).join(" and ")}` : "No winner") : g.status === "abandoned" ? "Abandoned" : "In progress"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface Review extends GameView {
  rounds_detail: {
    number: number;
    status: string;
    outcome: string | null;
    category_label: string | null;
    start_seconds: number | null;
    clip_seconds: number | null;
    timestamp_source: string | null;
    hints_used: string[];
    turn_player: string | null;
    movie: { title: string; release_year: number | null; poster_path: string | null; directors: string[] };
    guesses: { player: string; text: string; verdict: string; attempt: number }[];
    points: { player: string | null; points: number; reason: string }[];
  }[];
  adjustments: { player: string | null; points: number; note: string | null }[];
}

export function GameReview() {
  const { id } = useParams();
  const r = useApi<Review>(`/api/games/${id}/review`);
  if (r.error) return <ErrorNote error={r.error} />;
  if (!r.data) return <Spinner />;
  const g = r.data;
  const rows = g.team_mode && g.scoreboard.teams.length ? g.scoreboard.teams : g.scoreboard.players;
  return (
    <div className="max-w-5xl">
      <PageHeader
        title={g.name}
        sub={`${g.mode_label}, ${g.difficulty}, ${g.started_at ? new Date(g.started_at).toLocaleString() : "not started"}`}
        actions={<Link className="btn btn-sm" to="/history">All games</Link>}
      />
      <section className="surface p-4 mb-6">
        <ol className="flex flex-wrap gap-x-8 gap-y-2">
          {rows.map((s) => (
            <li key={s.id} className="flex items-baseline gap-2">
              <span className="text-dim num">{s.rank}.</span>
              <span>{s.name}</span>
              <span className="num font-semibold">{s.points}</span>
            </li>
          ))}
        </ol>
      </section>
      <ol className="space-y-3">
        {g.rounds_detail.map((rd) => (
          <li key={rd.number} className="surface p-4 flex gap-4">
            <Poster path={rd.movie.poster_path} title={rd.movie.title} size="w154" className="w-16 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-xs text-dim">
                Round {rd.number}{rd.category_label ? `, ${rd.category_label}` : ""}{rd.turn_player ? `, chosen by ${rd.turn_player}` : ""}
              </p>
              <p className="title text-xl">{rd.movie.title} <span className="text-dim text-base">{rd.movie.release_year}</span></p>
              <p className="text-sm text-dim num">
                {rd.status === "skipped" ? "Skipped" : `Scene at ${fmtTime(rd.start_seconds)}${rd.timestamp_source === "random" ? " (random)" : ""}, ${rd.clip_seconds ? `${rd.clip_seconds}s clip` : "continuous"}`}
                {rd.hints_used.length ? `, ${rd.hints_used.length} hint${rd.hints_used.length === 1 ? "" : "s"}` : ""}
              </p>
              {rd.guesses.length > 0 && (
                <ul className="mt-2 text-sm space-y-0.5">
                  {rd.guesses.map((x, i) => (
                    <li key={i} className="flex items-center gap-2">
                      {x.verdict === "correct" ? <Check className="size-3.5 text-hit" /> : x.verdict === "incorrect" ? <X className="size-3.5 text-miss" /> : <span className="size-3.5" />}
                      <span className="text-dim w-20 truncate">{x.player}</span> {x.text}
                    </li>
                  ))}
                </ul>
              )}
              {rd.points.length > 0 && (
                <p className="text-xs text-dim mt-2">Points: {rd.points.map((p) => `${p.player ?? "team"} ${p.points > 0 ? "+" : ""}${p.points} (${p.reason.replace("_", " ")})`).join(", ")}</p>
              )}
            </div>
          </li>
        ))}
      </ol>
      {g.adjustments.length > 0 && (
        <p className="text-sm text-dim mt-4">Manual adjustments: {g.adjustments.map((a) => `${a.player ?? "team"} ${a.points > 0 ? "+" : ""}${a.points}${a.note ? ` (${a.note})` : ""}`).join(", ")}</p>
      )}
    </div>
  );
}
