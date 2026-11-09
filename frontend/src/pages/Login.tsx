import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { ErrorNote } from "../components/ui";

export default function LoginPage({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <form
        className="surface w-full max-w-sm p-6 space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await api.post("/api/auth/host/login", { password });
            onDone();
          } catch (err) {
            setError(err as Error);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div>
          <h1 className="title text-3xl">Movie Roulette</h1>
          <p className="text-dim text-sm mt-1">Sign in as the host to run games and manage the library.</p>
        </div>
        <div>
          <label className="lbl" htmlFor="pw">Host password</label>
          <input id="pw" type="password" className="field" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
        </div>
        <ErrorNote error={error} />
        <button className="btn btn-lamp w-full" disabled={busy || !password}>Sign in</button>
        <p className="text-sm text-dim">
          Here to play? <Link className="text-lamp underline" to="/join">Join a game with a code</Link>
        </p>
      </form>
    </div>
  );
}
