import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Search } from "lucide-react";
import { api, type Category, type Facets, type GameView, type Player, type Team } from "../lib/api";
import { useApi } from "../lib/hooks";
import { Empty, PageHeader, Spinner, Toggle, useAction } from "../components/ui";

type FilterSel = { type: "category" | "genre" | "decade" | "collection" | "person" | "studio"; id: number; role?: string; label: string };

const MODES: { id: string; name: string; text: string }[] = [
  { id: "category", name: "Category Roulette", text: "Pick a category; the movie comes from it." },
  { id: "library", name: "Full Library Roulette", text: "Anything in the collection." },
  { id: "franchise", name: "Franchise Challenge", text: "Movies from one TMDB collection." },
  { id: "decade", name: "Decade Challenge", text: "Everything from one decade." },
  { id: "person", name: "Director or Actor", text: "Films by a director or with an actor." },
  { id: "custom", name: "Custom Game", text: "Combine filters with AND or OR." },
  { id: "difficulty", name: "Difficulty Challenge", text: "Clip length, replays and hints follow the difficulty." },
];
const CLIPS = [10, 15, 30, 45, 60];

interface SettingsResp {
  sections: {
    playback: { default_mode: "clip" | "continuous"; default_clip_seconds: number; continuous_max_seconds: number; video_on_player_devices: boolean };
    game: { default_difficulty: string; repeat_policy: string; default_rounds: number; default_category_ids: number[];
      difficulty_presets: Record<string, { clip_seconds: number; replays: number; hints: number; multiplier: number }> };
  };
}

export default function PlayPage() {
  const nav = useNavigate();
  const players = useApi<Player[]>("/api/players");
  const teams = useApi<Team[]>("/api/teams");
  const cats = useApi<Category[]>("/api/categories");
  const facets = useApi<Facets>("/api/facets");
  const settings = useApi<SettingsResp>("/api/settings");
  const { busy, run } = useAction();

  const [selected, setSelected] = useState<number[]>([]);
  const [teamMode, setTeamMode] = useState(false);
  const [teamOf, setTeamOf] = useState<Record<number, number | null>>({});
  const [newName, setNewName] = useState("");
  const [mode, setMode] = useState("category");
  const [filters, setFilters] = useState<FilterSel[]>([]);
  const [logic, setLogic] = useState<"all" | "any">("all");
  const [difficulty, setDifficulty] = useState("medium");
  const [pbMode, setPbMode] = useState<"clip" | "continuous">("clip");
  const [clip, setClip] = useState(30);
  const [customClip, setCustomClip] = useState("");
  const [contMax, setContMax] = useState(600);
  const [rounds, setRounds] = useState(10);
  const [repeat, setRepeat] = useState("game");
  const [videoOnPlayers, setVideoOnPlayers] = useState(true);
  const [walkIns, setWalkIns] = useState(true);
  const [name, setName] = useState("");
  const [personQuery, setPersonQuery] = useState("");
  const [people, setPeople] = useState<{ id: number; name: string; role: string; count: number }[]>([]);
  const [pool, setPool] = useState<{ eligible: number; in_filters: number; blocked_by_repeat: number } | null>(null);

  // defaults from settings, and a category passed from the home screen
  useEffect(() => {
    const s = settings.data?.sections;
    if (!s) return;
    setDifficulty(s.game.default_difficulty);
    setPbMode(s.playback.default_mode);
    setClip(s.playback.default_clip_seconds);
    setContMax(s.playback.continuous_max_seconds);
    setRounds(s.game.default_rounds);
    setRepeat(s.game.repeat_policy);
    setVideoOnPlayers(s.playback.video_on_player_devices);
  }, [settings.data]);
  useEffect(() => {
    if (!cats.data) return;
    const pre = new URLSearchParams(location.search).get("category");
    const ids = pre ? [Number(pre)] : settings.data?.sections.game.default_category_ids ?? [];
    const chosen = cats.data.filter((c) => ids.includes(c.id));
    if (chosen.length) {
      setMode("category");
      setFilters(chosen.map((c) => ({ type: "category", id: c.id, label: c.name })));
    }
  }, [cats.data, settings.data]);
  useEffect(() => {
    if (players.data && selected.length === 0) setSelected(players.data.map((p) => p.id));
    if (players.data) setTeamOf(Object.fromEntries(players.data.map((p) => [p.id, p.team_id])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [players.data]);
  useEffect(() => {
    if (mode !== "person" && mode !== "custom") return;
    const t = window.setTimeout(() => {
      api.get<typeof people>(`/api/people?q=${encodeURIComponent(personQuery)}`).then(setPeople).catch(() => {});
    }, 200);
    return () => window.clearTimeout(t);
  }, [personQuery, mode]);

  const sendFilters = useMemo(() => (mode === "library" ? [] : filters.map(({ type, id, role }) => ({ type, id, ...(role ? { role } : {}) }))), [filters, mode]);
  useEffect(() => {
    const t = window.setTimeout(() => {
      api.post<typeof pool>("/api/games/pool-preview", { filters: sendFilters, logic, repeat_policy: repeat === "game" ? "none" : repeat })
        .then(setPool)
        .catch(() => setPool(null));
    }, 150);
    return () => window.clearTimeout(t);
  }, [sendFilters, logic, repeat]);

  if (!players.data || !cats.data || !facets.data || !settings.data) return <Spinner />;
  const presets = settings.data.sections.game.difficulty_presets;

  const toggleFilter = (f: FilterSel, single = false) => {
    setFilters((cur) => {
      const has = cur.some((x) => x.type === f.type && x.id === f.id && x.role === f.role);
      if (has) return cur.filter((x) => !(x.type === f.type && x.id === f.id && x.role === f.role));
      return single ? [f] : [...cur, f];
    });
  };
  const isOn = (type: string, id: number, role?: string) => filters.some((x) => x.type === type && x.id === id && x.role === role);
  const chooseMode = (m: string) => {
    setMode(m);
    setFilters([]);
    if (m === "difficulty") setPbMode("clip");
  };
  const effectiveClip = mode === "difficulty" ? presets[difficulty]?.clip_seconds ?? clip : customClip ? Number(customClip) : clip;
  const needFilter = ["category", "franchise", "decade", "person", "custom"].includes(mode);
  const canStart = selected.length > 0 && (!needFilter || filters.length > 0) && (!teamMode || selected.every((id) => teamOf[id]));

  const start = () =>
    run(async () => {
      const g = await api.post<GameView>("/api/games", {
        name: name.trim() || undefined,
        mode,
        filters: sendFilters,
        logic,
        difficulty,
        playback_mode: mode === "difficulty" ? "clip" : pbMode,
        clip_seconds: effectiveClip,
        continuous_max_seconds: contMax,
        repeat_policy: repeat,
        rounds_planned: rounds,
        team_mode: teamMode,
        players: selected.map((id) => ({ player_id: id, team_id: teamMode ? teamOf[id] : null })),
        allow_walk_ins: walkIns,
        video_on_player_devices: videoOnPlayers,
      });
      nav(`/game/${g.id}`);
    });

  const catGroups: [string, Category[]][] = [
    ["Your categories", cats.data.filter((c) => c.kind === "custom")],
    ["Genres", cats.data.filter((c) => c.kind === "genre")],
    ["Decades", cats.data.filter((c) => c.kind === "decade")],
    ["Franchises", cats.data.filter((c) => c.kind === "collection")],
    ["Studios", cats.data.filter((c) => c.kind === "studio")],
  ];

  return (
    <div className="max-w-5xl">
      <PageHeader title="New game" sub="Set it up once; every round after that is one click." />
      <ol className="space-y-6">
        <Step n={1} title="Who's playing">
          {players.data.length === 0 && <p className="text-sm text-dim mb-3">No players yet. Add the first one below.</p>}
          <div className="flex flex-wrap gap-2">
            {players.data.map((p) => (
              <button
                key={p.id}
                className="chip"
                aria-pressed={selected.includes(p.id)}
                onClick={() => setSelected((s) => (s.includes(p.id) ? s.filter((x) => x !== p.id) : [...s, p.id]))}
              >
                <span className="size-2 rounded-full" style={{ background: p.color }} aria-hidden /> {p.name}
              </button>
            ))}
          </div>
          <form
            className="flex gap-2 mt-3 max-w-sm"
            onSubmit={(e) => {
              e.preventDefault();
              if (!newName.trim()) return;
              void run(async () => {
                const p = await api.post<Player>("/api/players", { name: newName.trim() });
                setNewName("");
                await players.reload();
                setSelected((s) => [...s, p.id]);
              });
            }}
          >
            <label htmlFor="np" className="sr-only">New player name</label>
            <input id="np" className="field" placeholder="Add a player" value={newName} onChange={(e) => setNewName(e.target.value)} />
            <button className="btn" disabled={busy || !newName.trim()}><Plus className="size-4" /> Add</button>
          </form>
          <div className="mt-3">
            <Toggle checked={teamMode} onChange={setTeamMode} label="Play in teams" hint="Points go to teams. Teams take turns choosing the scene." disabled={!teams.data?.length} />
            {!teams.data?.length && <p className="text-xs text-faint">Create teams on the Players page to play in teams.</p>}
            {teamMode && (
              <div className="grid sm:grid-cols-2 gap-2 mt-2">
                {players.data.filter((p) => selected.includes(p.id)).map((p) => (
                  <label key={p.id} className="flex items-center gap-2 text-sm">
                    <span className="w-28 truncate">{p.name}</span>
                    <select className="field" value={teamOf[p.id] ?? ""} onChange={(e) => setTeamOf((t) => ({ ...t, [p.id]: e.target.value ? Number(e.target.value) : null }))}>
                      <option value="">Choose a team</option>
                      {teams.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                  </label>
                ))}
              </div>
            )}
          </div>
        </Step>

        <Step n={2} title="Game mode">
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {MODES.map((m) => (
              <button
                key={m.id}
                onClick={() => chooseMode(m.id)}
                aria-pressed={mode === m.id}
                className={`text-left rounded-lg border px-4 py-3 transition-colors ${mode === m.id ? "border-lamp bg-lamp/10" : "border-line hover:bg-panel-2"}`}
              >
                <span className="block font-semibold text-sm">{m.name}</span>
                <span className="block text-xs text-dim mt-0.5">{m.text}</span>
              </button>
            ))}
          </div>
        </Step>

        {mode !== "library" && (
          <Step n={3} title={mode === "person" ? "Director or actor" : mode === "decade" ? "Decade" : mode === "franchise" ? "Franchise" : "Categories"}>
            {(mode === "category" || mode === "custom" || mode === "difficulty") && (
              <div className="space-y-3">
                {catGroups.filter(([, list]) => list.length).map(([label, list]) => (
                  <div key={label}>
                    <p className="text-xs text-dim mb-1.5">{label}</p>
                    <div className="flex flex-wrap gap-1.5">
                      {list.map((c) => (
                        <button key={c.id} className="chip" aria-pressed={isOn("category", c.id)} onClick={() => toggleFilter({ type: "category", id: c.id, label: c.name })}>
                          {c.name} <span className="text-faint num">{c.count}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
                {cats.data.length === 0 && <Empty title="No categories yet">Scan your library with a TMDB key to create them.</Empty>}
              </div>
            )}
            {mode === "franchise" && (
              <div className="flex flex-wrap gap-1.5">
                {facets.data.collections.map((c) => (
                  <button key={c.id} className="chip" aria-pressed={isOn("collection", c.id)} onClick={() => toggleFilter({ type: "collection", id: c.id, label: c.name }, true)}>
                    {c.name} <span className="text-faint num">{c.count}</span>
                  </button>
                ))}
                {!facets.data.collections.length && <p className="text-sm text-dim">No franchises found in the library.</p>}
              </div>
            )}
            {mode === "decade" && (
              <div className="flex flex-wrap gap-1.5">
                {facets.data.decades.map((d) => (
                  <button key={d.id} className="chip" aria-pressed={isOn("decade", d.id)} onClick={() => toggleFilter({ type: "decade", id: d.id, label: d.name }, true)}>
                    {d.name} <span className="text-faint num">{d.count}</span>
                  </button>
                ))}
              </div>
            )}
            {(mode === "person" || mode === "custom") && (
              <div className="mt-3">
                {mode === "custom" && <p className="text-xs text-dim mb-1.5">People</p>}
                <div className="relative max-w-sm">
                  <Search className="size-4 absolute left-3 top-3 text-faint" aria-hidden />
                  <input className="field pl-9" placeholder="Search a director or actor" value={personQuery} onChange={(e) => setPersonQuery(e.target.value)} aria-label="Search people" />
                </div>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {people.map((p) => (
                    <button
                      key={`${p.id}-${p.role}`}
                      className="chip"
                      aria-pressed={isOn("person", p.id, p.role)}
                      onClick={() => toggleFilter({ type: "person", id: p.id, role: p.role, label: p.name }, mode === "person")}
                    >
                      {p.name} <span className="text-faint">{p.role === "director" ? "director" : "actor"}, {p.count}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {mode === "custom" && (
              <div className="mt-4 space-y-3">
                <div className="flex flex-wrap gap-1.5">
                  {facets.data.decades.map((d) => (
                    <button key={d.id} className="chip" aria-pressed={isOn("decade", d.id)} onClick={() => toggleFilter({ type: "decade", id: d.id, label: d.name })}>{d.name}</button>
                  ))}
                </div>
                <fieldset className="flex gap-4 text-sm">
                  <legend className="lbl">Combine selections</legend>
                  <label className="flex items-center gap-2"><input type="radio" checked={logic === "all"} onChange={() => setLogic("all")} /> Must match all (AND)</label>
                  <label className="flex items-center gap-2"><input type="radio" checked={logic === "any"} onChange={() => setLogic("any")} /> Match any (OR)</label>
                </fieldset>
              </div>
            )}
            {mode === "category" && filters.length > 1 && (
              <fieldset className="flex gap-4 text-sm mt-3">
                <legend className="lbl">With several categories</legend>
                <label className="flex items-center gap-2"><input type="radio" checked={logic === "any"} onChange={() => setLogic("any")} /> Any of them</label>
                <label className="flex items-center gap-2"><input type="radio" checked={logic === "all"} onChange={() => setLogic("all")} /> All of them</label>
              </fieldset>
            )}
          </Step>
        )}

        <Step n={mode === "library" ? 3 : 4} title="Rules">
          <div className="grid md:grid-cols-2 gap-x-8 gap-y-4">
            <div>
              <span className="lbl">Difficulty</span>
              <div className="flex gap-2">
                {(["easy", "medium", "hard"] as const).map((d) => (
                  <button key={d} className="chip capitalize" aria-pressed={difficulty === d} onClick={() => setDifficulty(d)}>{d}</button>
                ))}
              </div>
              {presets[difficulty] && (
                <p className="text-xs text-dim mt-1.5">
                  {presets[difficulty].replays} replay{presets[difficulty].replays === 1 ? "" : "s"}, {presets[difficulty].hints} hint{presets[difficulty].hints === 1 ? "" : "s"}, points x{presets[difficulty].multiplier}
                  {mode === "difficulty" && `, ${presets[difficulty].clip_seconds}-second clips`}
                </p>
              )}
            </div>
            {mode !== "difficulty" && (
              <div>
                <span className="lbl">Playback</span>
                <div className="flex gap-2">
                  <button className="chip" aria-pressed={pbMode === "clip"} onClick={() => setPbMode("clip")}>Fixed clip</button>
                  <button className="chip" aria-pressed={pbMode === "continuous"} onClick={() => setPbMode("continuous")}>Continuous</button>
                </div>
                {pbMode === "clip" ? (
                  <div className="flex flex-wrap items-center gap-1.5 mt-2">
                    {CLIPS.map((c) => (
                      <button key={c} className="chip num" aria-pressed={!customClip && clip === c} onClick={() => { setClip(c); setCustomClip(""); }}>{c}s</button>
                    ))}
                    <input className="field w-24 num" placeholder="Custom" inputMode="numeric" value={customClip} onChange={(e) => setCustomClip(e.target.value.replace(/[^0-9]/g, ""))} aria-label="Custom clip length in seconds" />
                  </div>
                ) : (
                  <label className="flex items-center gap-2 mt-2 text-sm">
                    Stop after
                    <select className="field w-32" value={contMax} onChange={(e) => setContMax(Number(e.target.value))}>
                      {[120, 300, 600, 900, 1800].map((s) => <option key={s} value={s}>{s / 60} minutes</option>)}
                    </select>
                    or when someone gets it
                  </label>
                )}
              </div>
            )}
            <div>
              <label className="lbl" htmlFor="rounds">Rounds</label>
              <input id="rounds" type="number" min={1} max={200} className="field w-28 num" value={rounds} onChange={(e) => setRounds(Number(e.target.value) || 1)} />
            </div>
            <div>
              <label className="lbl" htmlFor="repeat">Repeats</label>
              <select id="repeat" className="field" value={repeat} onChange={(e) => setRepeat(e.target.value)}>
                <option value="game">No repeats in this game</option>
                <option value="session">No repeats since Movie Roulette started</option>
                <option value="days30">Skip movies played in the last 30 days</option>
                <option value="days90">Skip movies played in the last 90 days</option>
                <option value="none">Allow repeats</option>
              </select>
            </div>
            <div className="md:col-span-2 grid sm:grid-cols-2 gap-x-8">
              <Toggle checked={videoOnPlayers} onChange={setVideoOnPlayers} label="Show the video on players' phones" hint="Off: only the host screen plays the scene." />
              <Toggle checked={walkIns} onChange={setWalkIns} label="Let new people join with the code" />
            </div>
            <div className="md:col-span-2 max-w-sm">
              <label className="lbl" htmlFor="gname">Game name (optional)</label>
              <input id="gname" className="field" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="Friday movie night" />
            </div>
          </div>
        </Step>
      </ol>

      <div className="sticky bottom-20 md:bottom-4 mt-8 surface p-4 flex flex-wrap items-center justify-between gap-3 shadow-2xl">
        <p className="text-sm text-dim">
          {pool === null
            ? "Checking the library"
            : pool.eligible === 0
              ? "No playable movies match this selection yet."
              : `${pool.eligible} movie${pool.eligible === 1 ? "" : "s"} can come up${filters.length ? `: ${filters.map((f) => f.label).join(logic === "all" ? " and " : " or ")}` : ""}.`}
        </p>
        <button className="btn btn-lamp" disabled={busy || !canStart || pool?.eligible === 0} onClick={start}>
          Create game
        </button>
      </div>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="surface p-5 md:p-6">
      <h2 className="flex items-baseline gap-3 mb-4">
        <span className="title text-lamp text-2xl num">{n}</span>
        <span className="title text-2xl">{title}</span>
      </h2>
      {children}
    </li>
  );
}
