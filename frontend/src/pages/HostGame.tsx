import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import QRCode from "qrcode";
import {
  Check, Dices, Eye, EyeOff, Flag, Lightbulb, Pause, Play, Plus, RotateCcw, SkipForward, Square, Trophy, X,
} from "lucide-react";
import { api, ApiError, fmtTime, newRequestId, type GameView, type RoundView } from "../lib/api";
import { useApi, useGameSocket } from "../lib/hooks";
import { ErrorNote, Modal, Spinner, useAction } from "../components/ui";
import VideoStage from "../components/VideoStage";
import TimestampPicker from "../components/TimestampPicker";
import { Leaderboard, RevealCard, Scoreboard } from "../components/GameBits";

interface Net {
  lan_active: boolean;
  join_urls: string[];
  local_url: string;
}

export default function HostGamePage() {
  const { id } = useParams();
  const gameId = Number(id);
  const nav = useNavigate();
  const { state: live, setState, status, serverOffset } = useGameSocket(gameId, "host");
  const initial = useApi<GameView>(`/api/games/${gameId}`);
  const game = live ?? initial.data;
  const settings = useApi<{ sections: { playback: { default_volume: number } } }>("/api/settings");
  const { busy, run } = useAction();
  const [peek, setPeek] = useState(false);
  const [exhausted, setExhausted] = useState<Record<string, any> | null>(null);
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [confirmFinish, setConfirmFinish] = useState(false);

  useEffect(() => setPeek(false), [game?.round?.token]);

  if (initial.error) return <ErrorNote error={initial.error} />;
  if (!game) return <Spinner label="Loading the game" />;

  const r = game.round;
  const act = (path: string, body: unknown = {}, ok?: string) =>
    run(async () => {
      const s = await api.post<GameView>(path, body);
      setState(s);
      return s;
    }, ok);
  const nextRound = async (allowRepeat = false) => {
    setExhausted(null);
    try {
      const s = await api.post<GameView>(`/api/games/${gameId}/rounds`, { allow_repeat: allowRepeat });
      setState(s);
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "pool_exhausted") setExhausted(err.detail);
      else await run(() => Promise.reject(err));
    }
  };
  const finished = game.status === "finished" || game.status === "abandoned";
  const roundOpen = r && ["timestamp", "ready", "revealed"].includes(r.status);
  const lastPlanned = game.rounds_played >= game.rounds_planned;

  return (
    <div className="max-w-7xl">
      <header className="flex flex-wrap items-end justify-between gap-4 mb-5">
        <div>
          <p className="text-sm text-dim">
            {game.mode_label}
            {game.filter_labels.length > 0 && `: ${game.filter_labels.join(game.logic === "all" ? " and " : " or ")}`}
          </p>
          <h1 className="title text-3xl md:text-4xl">
            {finished ? game.name : r ? `Round ${r.number}` : game.name}
            {!finished && <span className="text-dim text-xl"> of {game.rounds_planned}</span>}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs ${status === "open" ? "text-hit" : "text-miss"}`}>{status === "open" ? "Live" : "Reconnecting"}</span>
          {!finished && (
            <>
              <button className="btn btn-sm" onClick={() => setAdjustOpen(true)}>Adjust score</button>
              <button className="btn btn-sm" onClick={() => setConfirmFinish(true)}>
                <Flag className="size-3.5" /> Finish game
              </button>
            </>
          )}
        </div>
      </header>

      {finished ? (
        <div className="grid lg:grid-cols-[1fr_320px] gap-6">
          <Leaderboard game={game} />
          <div className="space-y-3">
            <Link className="btn w-full" to={`/history/${game.id}`}>Review every round</Link>
            <Link className="btn btn-lamp w-full" to="/play">Start another game</Link>
          </div>
        </div>
      ) : (
        <div className="grid lg:grid-cols-[minmax(0,1fr)_320px] gap-6">
          <div className="space-y-5 min-w-0">
            <VideoStage playback={r && r.status !== "timestamp" && roundOpen ? r.playback : null} offset={serverOffset} defaultVolume={settings.data?.sections.playback.default_volume ?? 0.8} />

            {!roundOpen && (
              <section className="surface p-5 space-y-4">
                {r?.answer && <RevealCard answer={r.answer} outcome={r.outcome} />}
                {exhausted ? (
                  <div className="space-y-3">
                    <p className="font-semibold">{String(exhausted.message)}</p>
                    <div className="flex flex-wrap gap-2">
                      {exhausted.options?.includes("allow_repeats") && (
                        <button className="btn btn-lamp" onClick={() => nextRound(true)}>Allow a repeat for this round</button>
                      )}
                      <button className="btn" onClick={() => setConfirmFinish(true)}>Finish the game</button>
                      <Link className="btn btn-ghost" to="/play">Set up a game with other categories</Link>
                    </div>
                    <p className="text-xs text-dim">Movies outside your chosen categories are never picked automatically.</p>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-3">
                    <button className="btn btn-lamp text-base px-6 py-3" disabled={busy} onClick={() => nextRound()}>
                      <Dices className="size-5" /> {r ? "Spin the next round" : "Spin the first round"}
                    </button>
                    {lastPlanned && r && <span className="text-sm text-dim">That was the last planned round. Keep going or finish the game.</span>}
                  </div>
                )}
              </section>
            )}

            {r && roundOpen && <RoundControls game={game} r={r} busy={busy} act={act} peek={peek} setPeek={setPeek} />}
          </div>

          <aside className="space-y-5">
            <JoinPanel game={game} />
            <section className="surface p-3">
              <h2 className="title text-lg px-3 pt-1 pb-2">Scores</h2>
              <Scoreboard game={game} />
            </section>
            {game.history.length > 0 && (
              <section className="surface p-4">
                <h2 className="title text-lg mb-2">Earlier rounds</h2>
                <ul className="space-y-1.5 text-sm">
                  {[...game.history].reverse().map((h) => (
                    <li key={h.number} className="flex justify-between gap-3">
                      <span className="truncate">{h.number}. {h.title ?? "Not revealed"}</span>
                      <span className={h.outcome === "correct" ? "text-hit" : "text-dim"}>{h.outcome ?? h.status}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </aside>
        </div>
      )}

      <AdjustModal open={adjustOpen} onClose={() => setAdjustOpen(false)} game={game} onDone={setState} />
      <Modal open={confirmFinish} onClose={() => setConfirmFinish(false)} title="Finish this game?">
        <p className="text-dim text-sm">Scores are final once the game is finished. The join code stops working.</p>
        <div className="flex gap-2 mt-5">
          <button
            className="btn btn-lamp"
            onClick={async () => {
              setConfirmFinish(false);
              await act(`/api/games/${gameId}/finish`);
            }}
          >
            <Trophy className="size-4" /> Finish and show results
          </button>
          <button
            className="btn btn-ghost"
            onClick={async () => {
              setConfirmFinish(false);
              await act(`/api/games/${gameId}/finish?abandon=true`);
              nav("/");
            }}
          >
            Abandon without results
          </button>
        </div>
      </Modal>
    </div>
  );
}

function RoundControls({
  game, r, busy, act, peek, setPeek,
}: {
  game: GameView;
  r: RoundView;
  busy: boolean;
  act: (path: string, body?: unknown, ok?: string) => Promise<GameView | undefined>;
  peek: boolean;
  setPeek: (v: boolean) => void;
}) {
  const t = r.token;
  const pb = r.playback;
  const [spoken, setSpoken] = useState<number | "">("");
  const movie = r.host?.movie;
  const revealRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (r.status === "revealed") revealRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [r.status]);
  return (
    <div className="space-y-5">
      {r.status === "revealed" && r.answer && (
        <div ref={revealRef}>
          <RevealCard answer={r.answer} outcome={r.outcome} />
        </div>
      )}
      {/* the answer stays hidden on the host screen too, in case it is the TV everyone watches */}
      {r.status !== "revealed" && <section className="surface p-4 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-dim">{r.category_label}{r.turn_player && `, ${r.turn_player}'s turn`}</p>
          <p className={`title text-xl truncate ${peek ? "" : "blur-md select-none"}`} aria-hidden={!peek}>
            {peek && movie ? `${movie.title} (${movie.release_year ?? "?"})` : "Hidden answer placeholder"}
          </p>
        </div>
        <button className="btn btn-sm" onClick={() => setPeek(!peek)} aria-pressed={peek}>
          {peek ? <EyeOff className="size-4" /> : <Eye className="size-4" />} {peek ? "Hide answer" : "Show answer to me"}
        </button>
      </section>}

      {r.status === "timestamp" && (
        <section className="surface p-5">
          <h2 className="title text-2xl mb-1">Choose where the scene starts</h2>
          <p className="text-sm text-dim mb-4">
            {r.turn_player ? `${r.turn_player} can choose on their phone, or you can choose here.` : "Choose a start time."}
            {" "}The clip runs {r.clip_seconds ? `${r.clip_seconds} seconds` : "until someone gets it"}.
          </p>
          <TimestampPicker
            round={r}
            duration={r.host?.duration}
            busy={busy}
            onRandom={() => act(`/api/rounds/${t}/timestamp/random`)}
            onClearSuggestion={() => act(`/api/rounds/${t}/timestamp/clear`)}
            onConfirm={(seconds, source) => act(`/api/rounds/${t}/timestamp`, { seconds, source })}
          />
          <div className="mt-4 border-t border-line pt-3">
            <button className="btn btn-ghost btn-sm" onClick={() => act(`/api/rounds/${t}/close?skip=true`)}>
              <SkipForward className="size-4" /> Skip this movie
            </button>
          </div>
        </section>
      )}

      {r.status === "ready" && pb && (
        <section className="surface p-5 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {pb.state === "playing" ? (
              <button className="btn btn-lamp" disabled={busy} onClick={() => act(`/api/rounds/${t}/playback`, { action: "pause" })}>
                <Pause className="size-4" /> Pause
              </button>
            ) : (
              <button className="btn btn-lamp" disabled={busy || pb.media_status !== "ready"} onClick={() => act(`/api/rounds/${t}/playback`, { action: "play" })}>
                <Play className="size-4" /> {pb.state === "ended" ? "Play again" : pb.state === "paused" ? "Resume" : "Play the scene"}
              </button>
            )}
            <button className="btn" disabled={busy || pb.plays === 0 || pb.media_status !== "ready"} onClick={() => act(`/api/rounds/${t}/playback`, { action: "replay" })}>
              <RotateCcw className="size-4" /> Replay ({r.replays_left} left)
            </button>
            <button className="btn" disabled={busy || pb.state !== "playing"} onClick={() => act(`/api/rounds/${t}/playback`, { action: "stop" })}>
              <Square className="size-4" /> Stop
            </button>
            <button className="btn" disabled={busy || r.hints_left === 0} onClick={() => act(`/api/rounds/${t}/hint`)}>
              <Lightbulb className="size-4" /> Give a hint ({r.hints_left} left)
            </button>
            <span className="text-sm text-dim num ml-auto">
              Starts at {fmtTime(r.start_seconds)}
              {r.timestamp_source === "random" ? ", random" : ""}
            </span>
          </div>
          {r.hints.length > 0 && (
            <ul className="text-sm space-y-1">
              {r.hints.map((h) => <li key={h} className="flex gap-2"><Lightbulb className="size-4 text-lamp" aria-hidden /> {h}</li>)}
            </ul>
          )}
          <button className="text-xs text-dim underline" onClick={() => act(`/api/rounds/${t}/timestamp/clear`)}>
            Change the start time
          </button>
        </section>
      )}

      {(r.status === "ready" || r.status === "revealed") && (
        <section className="surface p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="title text-2xl">Guesses</h2>
            {r.status === "ready" && (
              <button className="btn btn-lamp" disabled={busy} onClick={() => act(`/api/rounds/${t}/reveal`)}>
                Reveal the movie
              </button>
            )}
          </div>
          {r.guesses.length === 0 && <p className="text-sm text-dim">Guesses typed on phones appear here. For spoken answers, mark the player below.</p>}
          <ul className="divide-y divide-line/60">
            {r.guesses.map((g) => (
              <li key={g.id} className="flex flex-wrap items-center gap-3 py-2">
                <span className="w-24 truncate text-sm text-dim">{g.player}</span>
                <span className={`flex-1 min-w-0 ${g.verdict === "incorrect" ? "line-through text-dim" : ""}`}>{g.text}</span>
                <div className="flex gap-1.5">
                  <button
                    className={`btn btn-sm ${g.verdict === "correct" ? "btn-hit" : ""}`}
                    disabled={busy}
                    aria-pressed={g.verdict === "correct"}
                    onClick={() => act(`/api/rounds/${t}/guesses/${g.id}/judge`, { verdict: "correct" })}
                  >
                    <Check className="size-3.5" /> Correct
                  </button>
                  <button
                    className={`btn btn-sm ${g.verdict === "incorrect" ? "btn-miss" : ""}`}
                    disabled={busy}
                    aria-pressed={g.verdict === "incorrect"}
                    onClick={() => act(`/api/rounds/${t}/guesses/${g.id}/judge`, { verdict: "incorrect" })}
                  >
                    <X className="size-3.5" /> Wrong
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {r.status === "ready" && (
            <form
              className="flex flex-wrap items-end gap-2 border-t border-line pt-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (spoken !== "") void act(`/api/rounds/${t}/correct`, { player_id: spoken }).then(() => setSpoken(""));
              }}
            >
              <div>
                <label className="lbl" htmlFor="spoken">Someone said it out loud</label>
                <select id="spoken" className="field" value={spoken} onChange={(e) => setSpoken(e.target.value ? Number(e.target.value) : "")}>
                  <option value="">Choose a player</option>
                  {game.players.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
              <button className="btn btn-hit" disabled={busy || spoken === ""}><Check className="size-4" /> Mark correct</button>
            </form>
          )}
        </section>
      )}

      {r.status === "revealed" && r.answer && (
        <>
          <section className="surface p-5 space-y-3">
            <h2 className="title text-xl">Bonus points</h2>
            <BonusRow game={game} token={t} act={act} busy={busy} />
            <div className="flex flex-wrap gap-2 pt-2 border-t border-line">
              <button className="btn btn-lamp" disabled={busy} onClick={() => act(`/api/rounds/${t}/close`)}>
                <SkipForward className="size-4" /> Close round
              </button>
              {r.playback && r.playback.media_status === "ready" && (
                <button className="btn" onClick={() => act(`/api/rounds/${t}/playback`, { action: "replay", force: true })}>
                  <RotateCcw className="size-4" /> Watch the scene again
                </button>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function BonusRow({ game, token, act, busy }: { game: GameView; token: string; act: (p: string, b?: unknown, ok?: string) => Promise<unknown>; busy: boolean }) {
  const [player, setPlayer] = useState<number | "">("");
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div>
        <label className="lbl" htmlFor="bp">Player</label>
        <select id="bp" className="field" value={player} onChange={(e) => setPlayer(e.target.value ? Number(e.target.value) : "")}>
          <option value="">Choose a player</option>
          {game.players.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </div>
      <button className="btn" disabled={busy || player === ""} onClick={() => act(`/api/rounds/${token}/bonus`, { kind: "year", player_id: player }, "Year bonus given.")}>
        <Plus className="size-4" /> Knew the year
      </button>
      <button className="btn" disabled={busy || player === ""} onClick={() => act(`/api/rounds/${token}/bonus`, { kind: "actor", player_id: player }, "Actor bonus given.")}>
        <Plus className="size-4" /> Named an actor
      </button>
    </div>
  );
}

function JoinPanel({ game }: { game: GameView }) {
  const net = useApi<Net>("/api/network");
  const [qr, setQr] = useState<string | null>(null);
  const url = net.data?.join_urls[0] ? `${net.data.join_urls[0]}?code=${game.join_code}` : null;
  useEffect(() => {
    if (!url) return setQr(null);
    QRCode.toDataURL(url, { margin: 1, width: 220, color: { dark: "#18171b", light: "#ede6da" } }).then(setQr).catch(() => setQr(null));
  }, [url]);
  if (!game.join_code) return null;
  return (
    <section className="surface p-4">
      <p className="text-sm text-dim">Join code</p>
      <p className="num title text-4xl tracking-[0.18em]">{game.join_code}</p>
      {net.data?.lan_active ? (
        <div className="mt-3 flex gap-3 items-start">
          {qr && <img src={qr} alt="QR code to join this game" className="w-28 rounded" />}
          <p className="text-xs text-dim break-all">Scan, or open {net.data.join_urls[0]} on a phone on the same Wi-Fi.</p>
        </div>
      ) : (
        <p className="text-xs text-dim mt-2">
          Phones can join once network access is on in <Link className="underline" to="/settings?tab=network">Settings</Link>.
        </p>
      )}
      <p className="text-xs text-faint mt-3">
        {game.players.filter((p) => p.online).length} of {game.players.length} players connected
      </p>
    </section>
  );
}

function AdjustModal({ open, onClose, game, onDone }: { open: boolean; onClose: () => void; game: GameView; onDone: (g: GameView) => void }) {
  const [who, setWho] = useState("");
  const [points, setPoints] = useState("1");
  const [note, setNote] = useState("");
  const [rid, setRid] = useState(newRequestId());
  const { busy, run } = useAction();
  useEffect(() => {
    if (open) setRid(newRequestId());
  }, [open]);
  return (
    <Modal open={open} onClose={onClose} title="Adjust a score">
      <div className="space-y-3">
        <div>
          <label className="lbl" htmlFor="aw">Who</label>
          <select id="aw" className="field" value={who} onChange={(e) => setWho(e.target.value)}>
            <option value="">Choose</option>
            {game.players.map((p) => <option key={`p${p.id}`} value={`p${p.id}`}>{p.name}</option>)}
            {game.teams.map((t) => <option key={`t${t.id}`} value={`t${t.id}`}>Team {t.name}</option>)}
          </select>
        </div>
        <div>
          <label className="lbl" htmlFor="ap">Points (use a minus sign to take points away)</label>
          <input id="ap" className="field num w-32" value={points} onChange={(e) => setPoints(e.target.value)} inputMode="decimal" />
        </div>
        <div>
          <label className="lbl" htmlFor="an">Reason (optional)</label>
          <input id="an" className="field" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
        </div>
        <button
          className="btn btn-lamp"
          disabled={busy || !who || Number.isNaN(Number(points)) || Number(points) === 0}
          onClick={() =>
            run(async () => {
              const body = {
                player_id: who.startsWith("p") ? Number(who.slice(1)) : null,
                team_id: who.startsWith("t") ? Number(who.slice(1)) : null,
                points: Number(points),
                note: note || null,
                request_id: rid,
              };
              const g = await api.post<GameView>(`/api/games/${game.id}/score`, body);
              onDone(g);
              onClose();
            }, "Score adjusted.")
          }
        >
          Apply
        </button>
      </div>
    </Modal>
  );
}
