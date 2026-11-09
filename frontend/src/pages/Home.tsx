import { Link, useNavigate } from "react-router-dom";
import { Clapperboard, Dices, RefreshCw } from "lucide-react";
import { api, type GameView } from "../lib/api";
import { useApi, useInterval } from "../lib/hooks";
import { ErrorNote, Poster, Spinner, StatusDot, useAction } from "../components/ui";

interface Status {
  library: { movies: number; needs_review: number; unmatched: number; files: number; problem_files: number; categories: number };
  scan: { running: boolean; status: string | null; phase: string | null; current: number; total: number; finished_at: string | null; message: string | null };
  tmdb: { configured: boolean };
  media: { ffmpeg: boolean; ffprobe: boolean };
  connected_players: number;
  recent_movies: { id: number; title: string; release_year: number | null; poster_path: string | null }[];
  recent_games: { id: number; name: string; status: string; mode: string; created_at: string }[];
  random_category: { id: number; name: string; kind: string; count: number } | null;
}

export default function HomePage() {
  const { data, error, reload } = useApi<Status>("/api/status");
  const active = useApi<{ game: GameView | null }>("/api/games/active");
  const nav = useNavigate();
  const { busy, run } = useAction();
  useInterval(reload, 3000, !!data?.scan.running);

  if (error) return <ErrorNote error={error} />;
  if (!data) return <Spinner />;
  const lib = data.library;
  const setupNeeded = !data.tmdb.configured || lib.files === 0 || !data.media.ffmpeg;

  return (
    <div className="space-y-10 max-w-6xl">
      <section className="grid gap-8 lg:grid-cols-[1.3fr_1fr] items-end">
        <div>
          <h1 className="title text-5xl md:text-6xl leading-[1.02]">
            {lib.movies > 0 ? `${lib.movies} movies, one scene at a time.` : "Point it at your movies."}
          </h1>
          <p className="text-dim mt-3 max-w-xl">
            {lib.movies > 0
              ? `Spread across ${lib.categories} categories. Pick a mode, choose a moment, and see who knows their films.`
              : "Add a movie folder and a TMDB key, scan, and the game builds itself from your own collection."}
          </p>
          <div className="flex flex-wrap gap-3 mt-6">
            {active.data?.game ? (
              <Link className="btn btn-lamp" to={`/game/${active.data.game.id}`}>
                <Clapperboard className="size-4" /> Return to {active.data.game.name}
              </Link>
            ) : (
              <Link className="btn btn-lamp" to="/play">
                <Clapperboard className="size-4" /> Start a game
              </Link>
            )}
            {data.random_category && (
              <button
                className="btn"
                disabled={busy}
                onClick={() => nav(`/play?category=${data.random_category!.id}`)}
              >
                <Dices className="size-4" /> Random category: {data.random_category.name}
              </button>
            )}
          </div>
        </div>
        <div className="surface p-5 space-y-2.5">
          <StatusDot ok={data.tmdb.configured} label={data.tmdb.configured ? "TMDB connected" : "TMDB key not set"} />
          <StatusDot ok={data.media.ffmpeg && data.media.ffprobe} label={data.media.ffmpeg ? "FFmpeg ready" : "FFmpeg not found"} />
          <StatusDot
            ok={data.scan.status === "failed" ? false : data.scan.status ? true : null}
            label={
              data.scan.running
                ? `Scanning: ${data.scan.phase} ${data.scan.total ? `${data.scan.current}/${data.scan.total}` : ""}`
                : data.scan.finished_at
                  ? `Last scan ${new Date(data.scan.finished_at).toLocaleString()}`
                  : "Library not scanned yet"
            }
          />
          <StatusDot ok={data.connected_players > 0 ? true : null} label={`${data.connected_players} player device${data.connected_players === 1 ? "" : "s"} connected`} />
          {lib.needs_review > 0 && (
            <Link to="/library?status=needs_review" className="block text-sm text-lamp underline pt-1">
              {lib.needs_review} movie{lib.needs_review === 1 ? "" : "s"} need a match check
            </Link>
          )}
          {lib.problem_files > 0 && (
            <Link to="/settings?tab=library" className="block text-sm text-dim underline">
              {lib.problem_files} file{lib.problem_files === 1 ? "" : "s"} could not be used
            </Link>
          )}
          <div className="pt-2">
            <button
              className="btn btn-sm"
              disabled={busy || data.scan.running}
              onClick={() => run(() => api.post("/api/scans", { mode: "scan" }).then(reload), "Library scan started.")}
            >
              <RefreshCw className={`size-3.5 ${data.scan.running ? "animate-spin" : ""}`} /> Rescan library
            </button>
          </div>
        </div>
      </section>

      {setupNeeded && (
        <section className="surface p-5">
          <h2 className="title text-xl">Finish setting up</h2>
          <ol className="mt-3 space-y-2 text-sm list-decimal list-inside text-dim">
            <li className={data.media.ffmpeg ? "line-through" : ""}>Install FFmpeg on this computer (see the README), then restart.</li>
            <li className={data.tmdb.configured ? "line-through" : ""}>
              Add your TMDB API key in <Link className="text-lamp underline" to="/settings?tab=tmdb">Settings</Link>.
            </li>
            <li className={lib.files > 0 ? "line-through" : ""}>
              Add a movie folder and scan it in <Link className="text-lamp underline" to="/settings?tab=library">Settings</Link>.
            </li>
          </ol>
        </section>
      )}

      {data.recent_movies.length > 0 && (
        <section>
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="title text-2xl">Recently added</h2>
            <Link to="/library" className="text-sm text-dim hover:text-screen">Open the library</Link>
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-3">
            {data.recent_movies.slice(0, 6).map((m) => (
              <Link key={m.id} to={`/library?movie=${m.id}`} className="group">
                <Poster path={m.poster_path} title={m.title} size="w185" />
                <p className="text-xs mt-1.5 text-dim group-hover:text-screen truncate">{m.title}</p>
              </Link>
            ))}
          </div>
        </section>
      )}

      {data.recent_games.length > 0 && (
        <section>
          <h2 className="title text-2xl mb-3">Recent games</h2>
          <ul className="divide-y divide-line/60 surface">
            {data.recent_games.map((g) => (
              <li key={g.id}>
                <Link
                  to={g.status === "active" || g.status === "setup" ? `/game/${g.id}` : `/history/${g.id}`}
                  className="flex items-center justify-between px-4 py-3 hover:bg-panel-2"
                >
                  <span>{g.name}</span>
                  <span className="text-sm text-dim">
                    {g.status === "active" ? "In progress" : g.status === "setup" ? "Not started" : new Date(g.created_at).toLocaleDateString()}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
