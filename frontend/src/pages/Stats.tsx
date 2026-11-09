import { useApi } from "../lib/hooks";
import { Empty, PageHeader, Spinner } from "../components/ui";

interface Stats {
  totals: { games: number; games_finished: number; rounds: number; rounds_played: number; correct: number; missed: number; skipped: number; success_rate: number; distinct_movies_played: number; movies: number };
  most_missed_movies: { movie_id: number; title: string; release_year: number | null; played: number; correct: number }[];
  hardest_genres: { name: string; played: number; correct: number; success_rate: number }[];
  hardest_decades: { name: string; played: number; correct: number; success_rate: number }[];
  players: { id: number; name: string; color: string; games: number; wins: number; win_rate: number; guesses: number; correct: number; accuracy: number; first_try: number; points: number; avg_points_per_game: number }[];
  teams: { id: number; name: string; color: string; games: number; wins: number; win_rate: number; points: number }[];
}

const pct = (x: number) => `${Math.round(x * 100)}%`;

export default function StatsPage() {
  const { data } = useApi<Stats>("/api/stats");
  if (!data) return <Spinner />;
  const t = data.totals;
  if (t.rounds === 0)
    return (
      <div className="max-w-5xl">
        <PageHeader title="Statistics" />
        <Empty title="Nothing to count yet">Play a game and the numbers appear here.</Empty>
      </div>
    );
  return (
    <div className="max-w-6xl space-y-10">
      <PageHeader title="Statistics" />
      <section>
        <p className="title text-3xl md:text-4xl leading-snug max-w-4xl">
          {t.games} game{t.games === 1 ? "" : "s"} and {t.rounds_played} rounds so far. Players named the movie {pct(t.success_rate)} of the time,
          across {t.distinct_movies_played} of your {t.movies} movies.
        </p>
        <p className="text-dim mt-2 text-sm">{t.correct} identified, {t.missed} missed, {t.skipped} skipped.</p>
      </section>

      <section>
        <h2 className="title text-2xl mb-3">Players</h2>
        <div className="surface overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-dim text-left">
              <tr className="border-b border-line">
                {["Player", "Games", "Wins", "Win rate", "Guesses", "Accuracy", "First try", "Points", "Per game"].map((h) => (
                  <th key={h} className="px-4 py-2 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="num">
              {data.players.map((p) => (
                <tr key={p.id} className="border-b border-line/50 last:border-0">
                  <td className="px-4 py-2"><span className="inline-block size-2 rounded-full mr-2" style={{ background: p.color }} />{p.name}</td>
                  <td className="px-4 py-2">{p.games}</td>
                  <td className="px-4 py-2">{p.wins}</td>
                  <td className="px-4 py-2">{pct(p.win_rate)}</td>
                  <td className="px-4 py-2">{p.guesses}</td>
                  <td className="px-4 py-2">{pct(p.accuracy)}</td>
                  <td className="px-4 py-2">{p.first_try}</td>
                  <td className="px-4 py-2">{p.points}</td>
                  <td className="px-4 py-2">{p.avg_points_per_game}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {data.teams.some((x) => x.games > 0) && (
        <section>
          <h2 className="title text-2xl mb-3">Teams</h2>
          <ul className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {data.teams.filter((x) => x.games > 0).map((x) => (
              <li key={x.id} className="surface p-4">
                <p className="flex items-center gap-2"><span className="size-2.5 rounded-full" style={{ background: x.color }} />{x.name}</p>
                <p className="text-sm text-dim mt-1 num">{x.wins} wins in {x.games} games ({pct(x.win_rate)}), {x.points} points</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid md:grid-cols-2 gap-8">
        <RateList title="Hardest genres" rows={data.hardest_genres} />
        <RateList title="By decade" rows={data.hardest_decades} />
      </div>

      {data.most_missed_movies.length > 0 && (
        <section>
          <h2 className="title text-2xl mb-3">Movies nobody gets</h2>
          <ul className="surface divide-y divide-line/60">
            {data.most_missed_movies.map((m) => (
              <li key={m.movie_id} className="flex justify-between px-4 py-2 text-sm">
                <span>{m.title} <span className="text-dim">{m.release_year}</span></span>
                <span className="text-dim num">missed {m.played - m.correct} of {m.played}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function RateList({ title, rows }: { title: string; rows: { name: string; played: number; correct: number; success_rate: number }[] }) {
  if (!rows.length) return null;
  return (
    <section>
      <h2 className="title text-2xl mb-3">{title}</h2>
      <ul className="space-y-2">
        {rows.map((r) => (
          <li key={r.name} className="text-sm">
            <div className="flex justify-between mb-1">
              <span>{r.name}</span>
              <span className="text-dim num">{r.correct} of {r.played} named</span>
            </div>
            <div className="h-1.5 rounded bg-panel-2 overflow-hidden">
              <div className="h-full bg-lamp" style={{ width: pct(r.success_rate) }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
