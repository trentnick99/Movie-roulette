import { Star } from "lucide-react";
import { fmtTime, img, type Answer, type GameView, type Standing } from "../lib/api";
import { Poster } from "./ui";

export function Scoreboard({ game, highlight }: { game: GameView; highlight?: number | null }) {
  const rows: Standing[] = game.team_mode && game.scoreboard.teams.length ? game.scoreboard.teams : game.scoreboard.players;
  const online = new Map(game.players.map((p) => [p.id, p.online]));
  return (
    <ol className="space-y-1.5" aria-label="Scoreboard">
      {rows.map((r) => (
        <li
          key={`${r.kind}-${r.id}`}
          className={`flex items-center gap-3 rounded-lg px-3 py-2 ${highlight === r.id ? "bg-panel-2" : ""}`}
        >
          <span className="num w-6 text-right text-dim text-sm">{r.tied ? `${r.rank}=` : r.rank}</span>
          <span className="size-2.5 rounded-full shrink-0" style={{ background: r.color }} aria-hidden />
          <span className="flex-1 truncate">
            {r.name}
            {r.kind === "player" && online.get(r.id) && <span className="sr-only"> (connected)</span>}
          </span>
          {r.kind === "player" && (
            <span className={`size-1.5 rounded-full ${online.get(r.id) ? "bg-hit" : "bg-transparent"}`} title={online.get(r.id) ? "Connected" : ""} />
          )}
          <span className="num font-semibold w-12 text-right">{Number.isInteger(r.points) ? r.points : r.points.toFixed(1)}</span>
        </li>
      ))}
      {game.team_mode && game.scoreboard.teams.length > 0 && (
        <li className="pt-2 text-xs text-faint">
          Players: {game.scoreboard.players.map((p) => `${p.name} ${p.points}`).join(", ")}
        </li>
      )}
    </ol>
  );
}

/** The title-card reveal: the one orchestrated animation in the app. */
export function RevealCard({ answer, outcome }: { answer: Answer; outcome?: string | null }) {
  const backdrop = img(answer.backdrop_path, "w1280");
  return (
    <section className="reveal-card surface overflow-hidden" aria-live="polite">
      {backdrop && (
        <div className="absolute inset-0 opacity-25" style={{ backgroundImage: `url(${backdrop})`, backgroundSize: "cover", backgroundPosition: "center" }} aria-hidden />
      )}
      <div className="absolute inset-0 bg-gradient-to-r from-panel via-panel/90 to-panel/40" aria-hidden />
      <div className="sweep" aria-hidden />
      <div className="relative flex gap-5 p-5 md:p-6">
        <Poster path={answer.poster_path} title={answer.title} className="w-28 md:w-36 shrink-0 shadow-2xl rise" />
        <div className="min-w-0">
          <p className="text-sm text-dim rise" style={{ animationDelay: "0.1s" }}>
            {outcome === "correct" ? "Got it." : outcome === "missed" ? "Nobody got this one." : "The answer"}
          </p>
          <h2 className="title text-3xl md:text-5xl leading-tight rise" style={{ animationDelay: "0.35s" }}>
            {answer.title}
          </h2>
          <p className="text-dim mt-1 rise" style={{ animationDelay: "0.55s" }}>
            {[answer.release_year, answer.directors.length ? `Directed by ${answer.directors.join(", ")}` : null]
              .filter(Boolean)
              .join(", ")}
          </p>
          <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm rise" style={{ animationDelay: "0.75s" }}>
            {answer.vote_average != null && (
              <span className="inline-flex items-center gap-1">
                <Star className="size-3.5 text-lamp" aria-hidden /> {answer.vote_average.toFixed(1)} on TMDB
              </span>
            )}
            {answer.genres.length > 0 && <span className="text-dim">{answer.genres.join(", ")}</span>}
            {answer.start_seconds != null && <span className="text-dim num">Scene at {fmtTime(answer.start_seconds)}</span>}
          </div>
          {answer.overview && (
            <p className="mt-3 text-sm text-dim max-w-prose line-clamp-3 rise" style={{ animationDelay: "0.9s" }}>
              {answer.overview}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

export function Leaderboard({ game }: { game: GameView }) {
  const rows = game.team_mode && game.scoreboard.teams.length ? game.scoreboard.teams : game.scoreboard.players;
  const winners = rows.filter((r) => r.rank === 1 && r.points > 0);
  return (
    <section className="surface p-6 md:p-8">
      <p className="text-dim text-sm">Final results</p>
      <h2 className="title text-4xl md:text-5xl mt-1">
        {winners.length === 0 ? "No winner this time" : winners.length === 1 ? `${winners[0].name} wins` : `Tie: ${winners.map((w) => w.name).join(" and ")}`}
      </h2>
      <ol className="mt-6 space-y-2">
        {rows.map((r) => (
          <li key={r.id} className="flex items-center gap-4 border-b border-line/60 pb-2">
            <span className="num text-2xl title w-10 text-right text-dim">{r.rank}</span>
            <span className="size-3 rounded-full" style={{ background: r.color }} aria-hidden />
            <span className="flex-1 text-lg">{r.name}</span>
            <span className="text-sm text-dim">{r.correct} correct</span>
            <span className="num text-xl font-semibold w-16 text-right">{r.points}</span>
          </li>
        ))}
      </ol>
      <p className="text-dim text-sm mt-4">{game.rounds_played} rounds played</p>
    </section>
  );
}
