import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { ErrorNote, TmdbAttribution } from "../components/ui";

export default function JoinPage() {
  const nav = useNavigate();
  const params = new URLSearchParams(location.search);
  const [code, setCode] = useState((params.get("code") || "").toUpperCase());
  const [name, setName] = useState(localStorage.getItem("mr-player-name") || "");
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<{ player: { game_status: string } | null }>("/api/auth/me")
      .then((me) => {
        if (me.player && ["setup", "active"].includes(me.player.game_status) && !params.get("code")) nav("/player");
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-5">
      <form
        className="w-full max-w-sm space-y-5"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await api.post("/api/join", { code: code.trim(), name: name.trim() });
            localStorage.setItem("mr-player-name", name.trim());
            nav("/player");
          } catch (err) {
            setError(err as Error);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div>
          <h1 className="title text-4xl">Join the game</h1>
          <p className="text-dim mt-1">The join code is on the host's screen.</p>
        </div>
        <div>
          <label className="lbl" htmlFor="code">Join code</label>
          <input
            id="code"
            className="field num text-2xl tracking-[0.3em] uppercase text-center"
            value={code}
            maxLength={10}
            autoComplete="off"
            autoCapitalize="characters"
            onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))}
          />
        </div>
        <div>
          <label className="lbl" htmlFor="name">Your name</label>
          <input id="name" className="field text-lg" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
        </div>
        <ErrorNote error={error} />
        <button className="btn btn-lamp w-full py-3 text-base" disabled={busy || code.length < 4 || !name.trim()}>
          Join
        </button>
      </form>
      <div className="mt-10 max-w-sm"><TmdbAttribution compact /></div>
    </div>
  );
}
