import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, type Player, type Team } from "../lib/api";
import { useApi } from "../lib/hooks";
import { Empty, PageHeader, Spinner, useAction } from "../components/ui";

export default function PlayersPage() {
  const players = useApi<Player[]>("/api/players");
  const teams = useApi<Team[]>("/api/teams");
  const { busy, run } = useAction();
  const [pname, setPname] = useState("");
  const [tname, setTname] = useState("");
  const [tsize, setTsize] = useState("");
  if (!players.data || !teams.data) return <Spinner />;
  const reload = async () => {
    await Promise.all([players.reload(), teams.reload()]);
  };

  return (
    <div className="max-w-5xl grid lg:grid-cols-[1.3fr_1fr] gap-8">
      <section>
        <PageHeader title="Players" sub="Players are remembered between games, so statistics follow them." />
        <form
          className="flex gap-2 mb-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api.post("/api/players", { name: pname.trim() });
              setPname("");
              await reload();
            });
          }}
        >
          <label htmlFor="pn" className="sr-only">Player name</label>
          <input id="pn" className="field" placeholder="Name" maxLength={40} value={pname} onChange={(e) => setPname(e.target.value)} />
          <button className="btn btn-lamp" disabled={busy || !pname.trim()}><Plus className="size-4" /> Add player</button>
        </form>
        {players.data.length === 0 ? (
          <Empty title="No players yet">Add everyone who plays. People can also join a game from their phone with its code.</Empty>
        ) : (
          <ul className="surface divide-y divide-line/60">
            {players.data.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                <input
                  type="color"
                  value={p.color}
                  aria-label={`Color for ${p.name}`}
                  className="size-7 rounded bg-transparent border-0 cursor-pointer"
                  onChange={(e) => run(async () => { await api.patch(`/api/players/${p.id}`, { color: e.target.value }); await reload(); })}
                />
                <input
                  className="field flex-1 min-w-32"
                  defaultValue={p.name}
                  aria-label="Player name"
                  maxLength={40}
                  onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== p.name && run(async () => { await api.patch(`/api/players/${p.id}`, { name: e.target.value.trim() }); await reload(); }, "Name saved.")}
                />
                <select
                  className="field w-40"
                  aria-label={`Team for ${p.name}`}
                  value={p.team_id ?? ""}
                  onChange={(e) => run(async () => {
                    await api.patch(`/api/players/${p.id}`, e.target.value ? { team_id: Number(e.target.value) } : { clear_team: true });
                    await reload();
                  })}
                >
                  <option value="">No team</option>
                  {teams.data!.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                <button
                  className="btn btn-ghost btn-sm"
                  aria-label={`Remove ${p.name}`}
                  disabled={busy}
                  onClick={() => confirm(`Remove ${p.name}? Their past games stay in the history.`) && run(async () => { await api.del(`/api/players/${p.id}`); await reload(); })}
                >
                  <Trash2 className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="title text-3xl mb-1 lg:mt-1">Teams</h2>
        <p className="text-dim text-sm mb-4">Optional. Turn on team play when setting up a game.</p>
        <form
          className="flex flex-wrap gap-2 mb-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api.post("/api/teams", { name: tname.trim(), max_size: tsize ? Number(tsize) : null });
              setTname("");
              setTsize("");
              await reload();
            });
          }}
        >
          <input className="field flex-1 min-w-32" placeholder="Team name" aria-label="Team name" maxLength={60} value={tname} onChange={(e) => setTname(e.target.value)} />
          <input className="field w-24 num" placeholder="Max size" aria-label="Maximum team size" inputMode="numeric" value={tsize} onChange={(e) => setTsize(e.target.value.replace(/\D/g, ""))} />
          <button className="btn" disabled={busy || !tname.trim()}><Plus className="size-4" /> Add team</button>
        </form>
        <ul className="space-y-3">
          {teams.data.map((t) => (
            <li key={t.id} className="surface p-4">
              <div className="flex items-center gap-3">
                <input
                  type="color"
                  value={t.color}
                  aria-label={`Color for ${t.name}`}
                  className="size-7 rounded bg-transparent border-0 cursor-pointer"
                  onChange={(e) => run(async () => { await api.patch(`/api/teams/${t.id}`, { color: e.target.value }); await reload(); })}
                />
                <input
                  className="field flex-1"
                  defaultValue={t.name}
                  aria-label="Team name"
                  onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== t.name && run(async () => { await api.patch(`/api/teams/${t.id}`, { name: e.target.value.trim() }); await reload(); })}
                />
                <input
                  className="field w-20 num"
                  defaultValue={t.max_size ?? ""}
                  placeholder="Any"
                  aria-label="Maximum size"
                  onBlur={(e) => run(async () => {
                    await api.patch(`/api/teams/${t.id}`, e.target.value ? { max_size: Number(e.target.value) } : { clear_max_size: true });
                    await reload();
                  })}
                />
                <button className="btn btn-ghost btn-sm" aria-label={`Delete team ${t.name}`} onClick={() => confirm(`Delete team ${t.name}?`) && run(async () => { await api.del(`/api/teams/${t.id}`); await reload(); })}>
                  <Trash2 className="size-4" />
                </button>
              </div>
              <p className="text-sm text-dim mt-2">
                {t.members.length ? t.members.map((m) => m.name).join(", ") : "No players yet. Pick this team next to a player's name."}
                {t.max_size ? ` (${t.members.length} of ${t.max_size})` : ""}
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
