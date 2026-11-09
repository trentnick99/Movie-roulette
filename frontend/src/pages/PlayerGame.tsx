import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check, Lightbulb, LogOut, Send, X } from "lucide-react";
import { api, type RoundView } from "../lib/api";
import { useGameSocket } from "../lib/hooks";
import { Empty, Spinner, useAction, useToast } from "../components/ui";
import VideoStage from "../components/VideoStage";
import TimestampPicker from "../components/TimestampPicker";
import { Leaderboard, RevealCard, Scoreboard } from "../components/GameBits";

/** The simplified, phone-first player screen. Shows only player-safe state from the server. */
export default function PlayerGamePage() {
  const [me, setMe] = useState<{ player_id: number; name: string; game_id: number } | null | undefined>(undefined);
  useEffect(() => {
    api
      .get<{ player: { player_id: number; name: string; game_id: number } | null }>("/api/auth/me")
      .then((r) => setMe(r.player))
      .catch(() => setMe(null));
  }, []);
  if (me === undefined) return <div className="p-6"><Spinner /></div>;
  if (me === null)
    return (
      <div className="p-6 max-w-md mx-auto mt-10">
        <Empty title="You're not in a game" action={<Link className="btn btn-lamp" to="/join">Join with a code</Link>} />
      </div>
    );
  return <PlayerScreen gameId={me.game_id} playerId={me.player_id} name={me.name} />;
}

function PlayerScreen({ gameId, playerId, name }: { gameId: number; playerId: number; name: string }) {
  const { state: game, status, authFailed, serverOffset, setState } = useGameSocket(gameId, "player");
  const { busy, run } = useAction();
  const toast = useToast();
  const [guess, setGuess] = useState("");
  const [showScores, setShowScores] = useState(false);

  if (authFailed)
    return (
      <div className="p-6 max-w-md mx-auto mt-10">
        <Empty title="This game has ended or you were signed out" action={<Link className="btn btn-lamp" to="/join">Join a game</Link>} />
      </div>
    );
  if (!game) return <div className="p-6"><Spinner label="Connecting to the host" /></div>;

  const r: RoundView | null = game.round;
  const post = (path: string, body: unknown = {}) =>
    run(async () => {
      const s = await api.post<typeof game>(path, body);
      setState(s);
      return s;
    });
  const mine = r?.guesses.filter((g) => g.player_id === playerId) ?? [];
  const solved = mine.some((g) => g.verdict === "correct");

  return (
    <div className="min-h-screen max-w-2xl mx-auto px-4 pt-4 pb-10 space-y-5">
      <header className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-dim truncate">{game.name}, playing as {name}</p>
          <h1 className="title text-2xl">
            {game.status === "finished" ? "Game over" : r ? `Round ${r.number}` : "Get ready"}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`size-2 rounded-full ${status === "open" ? "bg-hit" : "bg-miss animate-pulse"}`}
            title={status === "open" ? "Connected" : "Reconnecting"}
            aria-label={status === "open" ? "Connected" : "Reconnecting"}
          />
          <button className="btn btn-sm" onClick={() => setShowScores((s) => !s)} aria-expanded={showScores}>
            Scores
          </button>
          <button
            className="btn btn-ghost btn-sm"
            aria-label="Leave game"
            onClick={async () => {
              await api.post("/api/auth/player/leave");
              location.href = "/join";
            }}
          >
            <LogOut className="size-4" />
          </button>
        </div>
      </header>

      {showScores && (
        <div className="surface p-3">
          <Scoreboard game={game} highlight={playerId} />
        </div>
      )}

      {game.status === "finished" || game.status === "abandoned" ? (
        <Leaderboard game={game} />
      ) : !r || r.status === "closed" || r.status === "skipped" ? (
        <>
          {r?.answer && <RevealCard answer={r.answer} outcome={r.outcome} />}
          <div className="surface p-6 text-center">
            <p className="title text-xl">Waiting for the next round</p>
            <p className="text-dim text-sm mt-1">The host picks a movie at random. Keep this screen open.</p>
          </div>
        </>
      ) : (
        <>
          {r.category_label && <p className="text-sm text-dim">Category: {r.category_label}</p>}

          {r.status === "timestamp" &&
            (r.can_pick_timestamp && r.window ? (
              <section className="surface p-4 space-y-3">
                <h2 className="title text-xl">Your turn: choose where the scene starts</h2>
                <TimestampPicker
                  round={r}
                  busy={busy}
                  onRandom={() => post(`/api/rounds/${r.token}/timestamp/random`)}
                  onClearSuggestion={() => post(`/api/rounds/${r.token}/timestamp/clear`)}
                  onConfirm={(seconds, source) => post(`/api/rounds/${r.token}/timestamp`, { seconds, source })}
                />
              </section>
            ) : (
              <div className="surface p-6 text-center">
                <p className="title text-xl">{r.turn_player ? `${r.turn_player} is choosing the scene` : "The host is choosing the scene"}</p>
              </div>
            ))}

          {(r.status === "ready" || r.status === "revealed") && game.video_on_player_devices && (
            <VideoStage playback={r.playback} offset={serverOffset} startMuted showControlsNote="The host controls playback" />
          )}
          {r.status === "ready" && !game.video_on_player_devices && (
            <div className="surface p-6 text-center text-dim">Watch the main screen.</div>
          )}

          {r.hints.length > 0 && (
            <ul className="space-y-1">
              {r.hints.map((h) => (
                <li key={h} className="flex items-center gap-2 text-sm">
                  <Lightbulb className="size-4 text-lamp" aria-hidden /> {h}
                </li>
              ))}
            </ul>
          )}

          {r.status === "ready" && !solved && (
            <form
              className="flex gap-2"
              onSubmit={async (e) => {
                e.preventDefault();
                if (!guess.trim()) return;
                const ok = await post(`/api/rounds/${r.token}/guesses`, { text: guess.trim() });
                if (ok) {
                  setGuess("");
                  toast("Guess sent to the host.", "ok");
                }
              }}
            >
              <label htmlFor="guess" className="sr-only">Your guess</label>
              <input id="guess" className="field text-base" placeholder="Name the movie" value={guess} maxLength={300} onChange={(e) => setGuess(e.target.value)} autoComplete="off" />
              <button className="btn btn-lamp" disabled={busy || !guess.trim()}>
                <Send className="size-4" /> Guess
              </button>
            </form>
          )}
          {solved && r.status === "ready" && <p className="text-hit font-semibold">Correct. Wait for the reveal.</p>}

          {mine.length > 0 && (
            <ul className="space-y-1.5" aria-label="Your guesses">
              {mine.map((g) => (
                <li key={g.id} className="flex items-center gap-2 text-sm">
                  {g.verdict === "correct" ? <Check className="size-4 text-hit" /> : g.verdict === "incorrect" ? <X className="size-4 text-miss" /> : <span className="size-4 inline-block rounded-full border border-line" />}
                  <span className={g.verdict === "incorrect" ? "line-through text-dim" : ""}>{g.text}</span>
                  {g.verdict === "pending" && <span className="text-faint text-xs">waiting for the host</span>}
                </li>
              ))}
            </ul>
          )}

          {r.status === "revealed" && r.answer && <RevealCard answer={r.answer} outcome={r.outcome} />}
        </>
      )}
    </div>
  );
}
