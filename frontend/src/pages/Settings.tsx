import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { FolderOpen, FolderPlus, Play, RefreshCw, Square, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { useApi, useInterval } from "../lib/hooks";
import { ErrorNote, Modal, PageHeader, Spinner, StatusDot, TmdbAttribution, Toggle, useAction, useToast } from "../components/ui";

type Sections = Record<string, Record<string, any>>;
interface SettingsResp {
  sections: Sections;
  tmdb_key: { set: boolean; masked: string | null; source: string | null };
  host_password_set: boolean;
}

const TABS = [
  ["library", "Movie library"],
  ["tmdb", "TMDB"],
  ["playback", "Playback"],
  ["game", "Game rules"],
  ["scoring", "Scoring"],
  ["network", "Network"],
  ["database", "Database"],
  ["appearance", "Appearance"],
] as const;

export default function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "library";
  const s = useApi<SettingsResp>("/api/settings");
  return (
    <div className="max-w-5xl">
      <PageHeader title="Settings" />
      <div className="flex flex-wrap gap-1.5 mb-6" role="tablist">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className="chip" aria-pressed={tab === id} onClick={() => setParams({ tab: id }, { replace: true })}>
            {label}
          </button>
        ))}
      </div>
      {!s.data ? <Spinner /> : (
        <>
          {tab === "library" && <LibraryTab s={s.data} reload={s.reload} />}
          {tab === "tmdb" && <TmdbTab s={s.data} reload={s.reload} />}
          {tab === "playback" && <PlaybackTab s={s.data} reload={s.reload} />}
          {tab === "game" && <GameTab s={s.data} reload={s.reload} />}
          {tab === "scoring" && <ScoringTab s={s.data} reload={s.reload} />}
          {tab === "network" && <NetworkTab s={s.data} reload={s.reload} />}
          {tab === "database" && <DatabaseTab />}
          {tab === "appearance" && <AppearanceTab s={s.data} reload={s.reload} />}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ helpers */

function useSection(s: SettingsResp, name: string, reload: () => Promise<void>) {
  const [draft, setDraft] = useState<Record<string, any>>(s.sections[name]);
  useEffect(() => setDraft(s.sections[name]), [s, name]);
  const { busy, run } = useAction();
  const dirty = JSON.stringify(draft) !== JSON.stringify(s.sections[name]);
  const save = () =>
    run(async () => {
      await api.patch(`/api/settings/${name}`, draft);
      await reload();
    }, "Settings saved.");
  const set = (k: string, v: unknown) => setDraft((d) => ({ ...d, [k]: v }));
  return { draft, set, setDraft, save, busy, dirty };
}

function Card({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <section className="surface p-5 mb-5">
      <h2 className="title text-2xl mb-3">{title}</h2>
      {children}
      {footer && <div className="mt-4 pt-4 border-t border-line">{footer}</div>}
    </section>
  );
}

function Num({ label, value, onChange, step = 1, hint, width = "w-28" }: { label: string; value: number | null; onChange: (v: number | null) => void; step?: number; hint?: string; width?: string }) {
  const id = label.replace(/\W+/g, "-").toLowerCase();
  return (
    <div>
      <label className="lbl" htmlFor={id}>{label}</label>
      <input id={id} className={`field num ${width}`} type="number" step={step} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
      {hint && <p className="text-xs text-faint mt-1">{hint}</p>}
    </div>
  );
}

function SaveBar({ dirty, busy, save }: { dirty: boolean; busy: boolean; save: () => void }) {
  return (
    <button className="btn btn-lamp" disabled={!dirty || busy} onClick={save}>
      {dirty ? "Save changes" : "Saved"}
    </button>
  );
}

/* ------------------------------------------------------------------ library */

interface Dir { id: number; path: string; enabled: boolean; excluded_paths: string[]; reachable: boolean; files: number; last_scanned_at: string | null }
interface Scan { running: boolean; scan: { status: string; phase: string; current: number; total: number; counts: Record<string, number>; message: string | null; finished_at: string | null } | null }
interface Issue { id: number; path: string; filename: string; status: string; error: string | null }

function LibraryTab({ s, reload }: { s: SettingsResp; reload: () => Promise<void> }) {
  const dirs = useApi<Dir[]>("/api/directories");
  const scan = useApi<Scan>("/api/scans/current");
  const issues = useApi<Issue[]>("/api/files/issues");
  const dups = useApi<{ movie_id: number; title: string; files: { id: number; path: string; primary: boolean; identical_copy: boolean }[] }[]>("/api/files/duplicates");
  const sec = useSection(s, "library", reload);
  const { busy, run } = useAction();
  const [path, setPath] = useState("");
  const [browse, setBrowse] = useState<null | ((p: string) => void)>(null);
  const [editDir, setEditDir] = useState<Dir | null>(null);
  const running = !!scan.data?.running;
  useInterval(() => {
    void scan.reload();
  }, 1000, running);
  useEffect(() => {
    if (!running) {
      void issues.reload();
      void dups.reload();
      void dirs.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  const startScan = (mode = "scan") => run(async () => { await api.post("/api/scans", { mode }); await scan.reload(); });
  const sc = scan.data?.scan;

  return (
    <>
      <Card title="Movie folders">
        <p className="text-sm text-dim mb-3">Folders are scanned recursively. Movie Roulette only reads your files; it never renames, moves or deletes them.</p>
        <ul className="space-y-2 mb-4">
          {dirs.data?.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-house px-3 py-2">
              <StatusDot ok={d.reachable} label="" />
              <span className="flex-1 min-w-0 break-all text-sm">{d.path}</span>
              <span className="text-xs text-dim">{d.files} files{d.excluded_paths.length ? `, ${d.excluded_paths.length} excluded folder(s)` : ""}</span>
              <Toggle checked={d.enabled} label="Scan" onChange={(v) => run(async () => { await api.patch(`/api/directories/${d.id}`, { enabled: v }); await dirs.reload(); })} />
              <button className="btn btn-sm" onClick={() => setEditDir(d)}>Edit</button>
              <button className="btn btn-ghost btn-sm" aria-label={`Remove ${d.path}`} onClick={() => confirm("Remove this folder from the library? The files on disk are not touched.") && run(async () => { await api.del(`/api/directories/${d.id}`); await dirs.reload(); })}>
                <Trash2 className="size-4" />
              </button>
            </li>
          ))}
          {dirs.data?.length === 0 && <li className="text-sm text-dim">No folders yet.</li>}
        </ul>
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => { await api.post("/api/directories", { path }); setPath(""); await dirs.reload(); }, "Folder added. Scan to find its movies.");
          }}
        >
          <input className="field flex-1 min-w-60" placeholder="D:\Movies" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Folder path" />
          <button type="button" className="btn" onClick={() => setBrowse(() => (p: string) => setPath(p))}><FolderOpen className="size-4" /> Browse</button>
          <button className="btn btn-lamp" disabled={busy || !path.trim()}><FolderPlus className="size-4" /> Add folder</button>
        </form>
      </Card>

      <Card title="Scanning">
        <div className="flex flex-wrap items-center gap-3">
          {running ? (
            <button className="btn" onClick={() => run(async () => { await api.post("/api/scans/cancel"); })}><Square className="size-4" /> Stop scan</button>
          ) : (
            <>
              <button className="btn btn-lamp" disabled={busy} onClick={() => startScan()}><Play className="size-4" /> Scan now</button>
              <button className="btn" disabled={busy || !s.tmdb_key.set} onClick={() => startScan("match")}><RefreshCw className="size-4" /> Retry unmatched movies</button>
            </>
          )}
          {sc && (
            <span className="text-sm text-dim">
              {running ? `${sc.phase}${sc.total ? ` ${sc.current} of ${sc.total}` : ""}` : `Last scan ${sc.status}${sc.finished_at ? `, ${new Date(sc.finished_at).toLocaleString()}` : ""}`}
            </span>
          )}
        </div>
        {running && sc && sc.total > 0 && (
          <div className="h-1.5 rounded bg-panel-2 overflow-hidden mt-3" role="progressbar" aria-valuenow={sc.current} aria-valuemax={sc.total}>
            <div className="h-full bg-lamp transition-all" style={{ width: `${(sc.current / sc.total) * 100}%` }} />
          </div>
        )}
        {sc?.message && <p className="text-sm text-miss mt-2">{sc.message}</p>}
        {sc && (
          <p className="text-xs text-dim mt-3">
            {Object.entries(sc.counts).filter(([, v]) => v).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ") || "No changes."}
          </p>
        )}
        <div className="grid sm:grid-cols-3 gap-4 mt-5">
          <Num label="Rescan automatically every (minutes)" value={sec.draft.auto_rescan_minutes} onChange={(v) => sec.set("auto_rescan_minutes", v ?? 0)} hint="0 turns automatic rescans off." />
          <Num label="Ignore files shorter than (minutes)" value={sec.draft.min_duration_minutes} onChange={(v) => sec.set("min_duration_minutes", v ?? 0)} hint="Skips samples, trailers and extras." />
          <Num label="Auto-accept matches at confidence" value={sec.draft.auto_accept_confidence} onChange={(v) => sec.set("auto_accept_confidence", v ?? 85)} hint="Lower matches wait for you." />
        </div>
        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <div>
            <label className="lbl" htmlFor="skip">Skip folders named</label>
            <input
              id="skip"
              className="field"
              key={sec.draft.skip_folder_names.join("|")}
              defaultValue={sec.draft.skip_folder_names.join(", ")}
              onBlur={(e) => sec.set("skip_folder_names", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))}
            />
            <p className="text-xs text-faint mt-1">Separate names with commas.</p>
          </div>
          <div>
            <label className="lbl" htmlFor="dup">With duplicate copies, play</label>
            <select id="dup" className="field" value={sec.draft.duplicate_preference} onChange={(e) => sec.set("duplicate_preference", e.target.value)}>
              <option value="highest_resolution">The highest resolution copy</option>
              <option value="largest">The largest file</option>
              <option value="first_found">The first copy found</option>
            </select>
          </div>
        </div>
        <div className="mt-4"><SaveBar {...sec} /></div>
      </Card>

      {(issues.data?.length ?? 0) > 0 && (
        <Card title="Files that could not be used">
          <ul className="space-y-1.5 text-sm max-h-80 overflow-y-auto scroll-thin">
            {issues.data!.map((i) => (
              <li key={i.id} className="rounded bg-house px-3 py-2">
                <span className="break-all">{i.path}</span>
                <span className="block text-xs text-dim">{i.status}: {i.error}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {(dups.data?.length ?? 0) > 0 && (
        <Card title="Movies with more than one copy">
          <ul className="space-y-2 text-sm">
            {dups.data!.map((d) => (
              <li key={d.movie_id}>
                <p>{d.title}</p>
                <ul className="text-xs text-dim">
                  {d.files.map((f) => <li key={f.id} className="break-all">{f.primary ? "Used for games: " : ""}{f.path}{f.identical_copy ? " (identical copy)" : ""}</li>)}
                </ul>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {browse && <FolderBrowser onClose={() => setBrowse(null)} onPick={(p) => { browse(p); setBrowse(null); }} />}
      {editDir && <DirEditor dir={editDir} onClose={() => setEditDir(null)} onSaved={() => { setEditDir(null); void dirs.reload(); }} />}
    </>
  );
}

function FolderBrowser({ onClose, onPick, start }: { onClose: () => void; onPick: (p: string) => void; start?: string }) {
  const [path, setPath] = useState<string | null>(start ?? null);
  const data = useApi<{ path: string | null; parent: string | null; folders: { name: string; path: string }[] }>(`/api/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`);
  return (
    <Modal open onClose={onClose} title="Choose a folder">
      <p className="text-sm text-dim break-all mb-2">{data.data?.path ?? "This computer"}</p>
      <ErrorNote error={data.error} />
      <ul className="max-h-80 overflow-y-auto scroll-thin border border-line rounded-lg divide-y divide-line/60">
        {data.data?.path && (
          <li><button className="w-full text-left px-3 py-2 hover:bg-panel-2 text-dim" onClick={() => setPath(data.data!.parent)}>Up one level</button></li>
        )}
        {data.data?.folders.map((f) => (
          <li key={f.path}><button className="w-full text-left px-3 py-2 hover:bg-panel-2" onClick={() => setPath(f.path)}>{f.name}</button></li>
        ))}
      </ul>
      <div className="flex gap-2 mt-4">
        <button className="btn btn-lamp" disabled={!data.data?.path} onClick={() => onPick(data.data!.path!)}>Use this folder</button>
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

function DirEditor({ dir, onClose, onSaved }: { dir: Dir; onClose: () => void; onSaved: () => void }) {
  const [path, setPath] = useState(dir.path);
  const [excl, setExcl] = useState<string[]>(dir.excluded_paths);
  const [browse, setBrowse] = useState<null | "path" | "exclude">(null);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title="Edit movie folder">
      <div className="space-y-4">
        <div>
          <label className="lbl" htmlFor="dp">Folder</label>
          <div className="flex gap-2">
            <input id="dp" className="field" value={path} onChange={(e) => setPath(e.target.value)} />
            <button className="btn" onClick={() => setBrowse("path")}><FolderOpen className="size-4" /></button>
          </div>
        </div>
        <div>
          <span className="lbl">Excluded sub-folders</span>
          <ul className="space-y-1 mb-2">
            {excl.map((e) => (
              <li key={e} className="flex items-center gap-2 text-sm">
                <span className="flex-1 break-all">{e}</span>
                <button className="btn btn-ghost btn-sm" aria-label="Remove exclusion" onClick={() => setExcl(excl.filter((x) => x !== e))}><Trash2 className="size-4" /></button>
              </li>
            ))}
            {!excl.length && <li className="text-sm text-dim">None</li>}
          </ul>
          <button className="btn btn-sm" onClick={() => setBrowse("exclude")}>Exclude a sub-folder</button>
        </div>
        <button className="btn btn-lamp" disabled={busy} onClick={() => run(async () => { await api.patch(`/api/directories/${dir.id}`, { path, excluded_paths: excl }); onSaved(); }, "Folder saved.")}>Save</button>
      </div>
      {browse && (
        <FolderBrowser
          start={dir.path}
          onClose={() => setBrowse(null)}
          onPick={(p) => { if (browse === "path") setPath(p); else setExcl([...new Set([...excl, p])]); setBrowse(null); }}
        />
      )}
    </Modal>
  );
}

/* ------------------------------------------------------------------ TMDB */

function TmdbTab({ s, reload }: { s: SettingsResp; reload: () => Promise<void> }) {
  const [key, setKey] = useState("");
  const { busy, run } = useAction();
  const sec = useSection(s, "tmdb", reload);
  const cache = useApi<{ responses: number; images: number; image_bytes: number; stream_bytes: number }>("/api/settings/tmdb/cache");
  return (
    <>
      <Card title="TMDB API key">
        <p className="text-sm text-dim mb-3">
          Movie Roulette identifies your movies with The Movie Database. Create a free account at themoviedb.org, then copy the
          "API Key" or "API Read Access Token" from your account settings (API section) and paste it here.
        </p>
        <p className="text-sm mb-3">
          {s.tmdb_key.set ? <>Key saved <span className="num text-dim">{s.tmdb_key.masked}</span>{s.tmdb_key.source === "environment" && " (from the .env file)"}</> : "No key saved."}
        </p>
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api.post("/api/settings/tmdb/key", { key }); setKey(""); await reload(); }, "Key checked with TMDB and saved."); }}>
          <input className="field flex-1 min-w-60" type="password" autoComplete="off" placeholder="Paste your TMDB key" value={key} onChange={(e) => setKey(e.target.value)} aria-label="TMDB API key" />
          <button className="btn btn-lamp" disabled={busy || key.trim().length < 10}>Check and save</button>
        </form>
        {s.tmdb_key.set && (
          <div className="flex gap-2 mt-3">
            <button className="btn btn-sm" disabled={busy} onClick={() => run(async () => { await api.post("/api/settings/tmdb/test"); }, "TMDB connection works.")}>Test connection</button>
            {s.tmdb_key.source === "settings" && <button className="btn btn-ghost btn-sm" onClick={() => run(async () => { await api.del("/api/settings/tmdb/key"); await reload(); }, "Key removed.")}>Remove key</button>}
          </div>
        )}
        <p className="text-xs text-faint mt-3">The key is stored only on this computer{navigator.userAgent.includes("Windows") ? ", encrypted for your Windows account" : ""}, and is never shown again or sent anywhere except TMDB.</p>
      </Card>
      <Card title="Metadata" footer={<SaveBar {...sec} />}>
        <div className="grid sm:grid-cols-3 gap-4">
          <div>
            <label className="lbl" htmlFor="lang">Language</label>
            <input id="lang" className="field w-32" value={sec.draft.language} onChange={(e) => sec.set("language", e.target.value)} />
          </div>
          <Num label="Keep TMDB answers for (days)" value={sec.draft.cache_days} onChange={(v) => sec.set("cache_days", v ?? 30)} />
          <Num label="Cast members to store" value={sec.draft.cast_limit} onChange={(v) => sec.set("cast_limit", v ?? 15)} />
        </div>
        <Toggle checked={sec.draft.include_adult} onChange={(v) => sec.set("include_adult", v)} label="Include adult titles in TMDB searches" />
        <div className="flex flex-wrap gap-2 mt-3">
          <button className="btn btn-sm" disabled={busy || !s.tmdb_key.set} onClick={() => run(async () => { await api.post("/api/scans", { mode: "refresh" }); }, "Refreshing metadata in the background.")}>
            <RefreshCw className="size-3.5" /> Refresh all metadata
          </button>
        </div>
      </Card>
      <Card title="Cache">
        {cache.data && (
          <p className="text-sm text-dim">
            {cache.data.responses} saved TMDB answers, {cache.data.images} images ({(cache.data.image_bytes / 1e6).toFixed(1)} MB),
            {" "}{(cache.data.stream_bytes / 1e6).toFixed(1)} MB of round clips.
          </p>
        )}
        <button className="btn btn-sm mt-3" disabled={busy} onClick={() => run(async () => { await api.del("/api/settings/tmdb/cache"); await cache.reload(); }, "Cache cleared.")}>Clear cache</button>
      </Card>
      <TmdbAttribution />
    </>
  );
}

/* ------------------------------------------------------------------ playback & game & scoring */

function PlaybackTab({ s, reload }: { s: SettingsResp; reload: () => Promise<void> }) {
  const sec = useSection(s, "playback", reload);
  const d = sec.draft;
  return (
    <Card title="Playback" footer={<SaveBar {...sec} />}>
      <div className="grid sm:grid-cols-2 gap-5">
        <div>
          <span className="lbl">Default playback</span>
          <div className="flex gap-2">
            <button className="chip" aria-pressed={d.default_mode === "clip"} onClick={() => sec.set("default_mode", "clip")}>Fixed clip</button>
            <button className="chip" aria-pressed={d.default_mode === "continuous"} onClick={() => sec.set("default_mode", "continuous")}>Continuous</button>
          </div>
        </div>
        <Num label="Default clip length (seconds)" value={d.default_clip_seconds} onChange={(v) => sec.set("default_clip_seconds", v ?? 30)} />
        <Num label="Continuous playback stops after (seconds)" value={d.continuous_max_seconds} onChange={(v) => sec.set("continuous_max_seconds", v ?? 600)} />
        <div>
          <label className="lbl" htmlFor="vol">Default volume</label>
          <input id="vol" type="range" min={0} max={1} step={0.05} value={d.default_volume} onChange={(e) => sec.set("default_volume", Number(e.target.value))} className="w-48 accent-[var(--color-lamp)]" />
        </div>
        <div>
          <label className="lbl" htmlFor="res">Stream quality</label>
          <select id="res" className="field" value={d.max_resolution} onChange={(e) => sec.set("max_resolution", Number(e.target.value))}>
            <option value={1080}>Up to 1080p</option>
            <option value={720}>Up to 720p (slower computers, busy Wi-Fi)</option>
            <option value={480}>Up to 480p</option>
          </select>
        </div>
        <div>
          <label className="lbl" htmlFor="pre">Clip preparation speed</label>
          <select id="pre" className="field" value={d.encoder_preset} onChange={(e) => sec.set("encoder_preset", e.target.value)}>
            <option value="ultrafast">Fastest (larger stream)</option>
            <option value="superfast">Very fast</option>
            <option value="veryfast">Fast (recommended)</option>
            <option value="faster">Balanced</option>
            <option value="fast">Best quality (needs a fast computer)</option>
          </select>
        </div>
        <div>
          <label className="lbl" htmlFor="al">Preferred audio language</label>
          <input id="al" className="field w-28" value={d.preferred_audio_language} onChange={(e) => sec.set("preferred_audio_language", e.target.value)} />
        </div>
      </div>
      <Toggle checked={d.video_on_player_devices} onChange={(v) => sec.set("video_on_player_devices", v)} label="Show the video on players' phones by default" />
      <p className="text-xs text-faint mt-3">
        Any format FFmpeg can read works (MKV, MP4, AVI, MOV, M4V, WMV, WebM, MPEG-TS and more). For each round only the needed
        part of the movie is converted for the browser; whole movies are never converted.
      </p>
    </Card>
  );
}

function GameTab({ s, reload }: { s: SettingsResp; reload: () => Promise<void> }) {
  const sec = useSection(s, "game", reload);
  const d = sec.draft;
  const cats = useApi<{ id: number; name: string }[]>("/api/categories");
  const setPreset = (lvl: string, k: string, v: number | null) =>
    sec.set("difficulty_presets", { ...d.difficulty_presets, [lvl]: { ...d.difficulty_presets[lvl], [k]: v ?? 0 } });
  return (
    <>
      <Card title="Choosing the scene" footer={<SaveBar {...sec} />}>
        <div className="grid sm:grid-cols-2 gap-x-6">
          <Toggle checked={d.timestamp_manual} onChange={(v) => sec.set("timestamp_manual", v)} label="Allow picking a time by hand" />
          <Toggle checked={d.timestamp_random} onChange={(v) => sec.set("timestamp_random", v)} label="Allow random times" />
          <Toggle checked={d.player_timestamp_from_device} onChange={(v) => sec.set("player_timestamp_from_device", v)} label="Players choose the time on their own phone" />
          <Toggle checked={d.host_override} onChange={(v) => sec.set("host_override", v)} label="Host can change a confirmed time" />
          <Toggle checked={d.exclude_opening} onChange={(v) => sec.set("exclude_opening", v)} label="Skip opening credits" hint="Uses chapter markers when they name the credits, otherwise the time below." />
          <Toggle checked={d.exclude_ending} onChange={(v) => sec.set("exclude_ending", v)} label="Skip end credits" />
          <Toggle checked={d.use_chapters} onChange={(v) => sec.set("use_chapters", v)} label="Use chapter markers to find credits" />
        </div>
        <div className="grid sm:grid-cols-4 gap-4 mt-3">
          <Num label="Opening credits (seconds)" value={d.opening_seconds} onChange={(v) => sec.set("opening_seconds", v ?? 0)} />
          <Num label="End credits (seconds)" value={d.ending_seconds} onChange={(v) => sec.set("ending_seconds", v ?? 0)} />
          <Num label="Earliest start (seconds)" value={d.min_timestamp_seconds} onChange={(v) => sec.set("min_timestamp_seconds", v ?? 0)} />
          <Num label="Latest start (seconds)" value={d.max_timestamp_seconds} onChange={(v) => sec.set("max_timestamp_seconds", v)} hint="Empty means the end of the movie." />
        </div>
      </Card>

      <Card title="Difficulty" footer={<SaveBar {...sec} />}>
        <div className="overflow-x-auto">
          <table className="text-sm">
            <thead className="text-dim text-left"><tr>{["", "Clip (s)", "Replays", "Hints", "Start from %", "Up to %", "Points x"].map((h) => <th key={h} className="pr-3 pb-2 font-medium">{h}</th>)}</tr></thead>
            <tbody>
              {(["easy", "medium", "hard"] as const).map((lvl) => (
                <tr key={lvl}>
                  <td className="pr-3 capitalize">{lvl}</td>
                  {(["clip_seconds", "replays", "hints", "range_start_pct", "range_end_pct", "multiplier"] as const).map((k) => (
                    <td key={k} className="pr-3 py-1">
                      <input className="field num w-20" type="number" step={k === "multiplier" ? 0.1 : 1} aria-label={`${lvl} ${k}`} value={d.difficulty_presets[lvl][k]} onChange={(e) => setPreset(lvl, k, e.target.value === "" ? null : Number(e.target.value))} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-faint mt-2">Difficulty never guesses how famous a scene is; it only changes these rules.</p>
        <div className="grid sm:grid-cols-3 gap-4 mt-4">
          <div>
            <label className="lbl" htmlFor="dd">Default difficulty</label>
            <select id="dd" className="field" value={d.default_difficulty} onChange={(e) => sec.set("default_difficulty", e.target.value)}>
              <option value="easy">Easy</option><option value="medium">Medium</option><option value="hard">Hard</option>
            </select>
          </div>
          <Num label="Default number of rounds" value={d.default_rounds} onChange={(v) => sec.set("default_rounds", v ?? 10)} />
          <div>
            <label className="lbl" htmlFor="gm">Who may guess</label>
            <select id="gm" className="field" value={d.guess_mode} onChange={(e) => sec.set("guess_mode", e.target.value)}>
              <option value="everyone">Everyone, every round</option>
              <option value="turn_only">Only the player or team whose turn it is</option>
            </select>
          </div>
        </div>
        <Toggle checked={d.hints_enabled} onChange={(v) => sec.set("hints_enabled", v)} label="Hints" />
      </Card>

      <Card title="Repeats and defaults" footer={<SaveBar {...sec} />}>
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className="lbl" htmlFor="rp">Default repeat rule</label>
            <select id="rp" className="field" value={d.repeat_policy} onChange={(e) => sec.set("repeat_policy", e.target.value)}>
              <option value="game">No repeats in a game</option>
              <option value="session">No repeats since Movie Roulette started</option>
              <option value="days30">Skip movies played in the last 30 days</option>
              <option value="days90">Skip movies played in the last 90 days</option>
              <option value="none">Allow repeats</option>
            </select>
          </div>
          <div>
            <span className="lbl">Categories selected when setting up a game</span>
            <div className="flex flex-wrap gap-1.5">
              {cats.data?.map((c) => (
                <button key={c.id} className="chip" aria-pressed={d.default_category_ids.includes(c.id)} onClick={() => sec.set("default_category_ids", d.default_category_ids.includes(c.id) ? d.default_category_ids.filter((x: number) => x !== c.id) : [...d.default_category_ids, c.id])}>{c.name}</button>
              ))}
            </div>
          </div>
        </div>
      </Card>
    </>
  );
}

function ScoringTab({ s, reload }: { s: SettingsResp; reload: () => Promise<void> }) {
  const sec = useSection(s, "scoring", reload);
  const d = sec.draft;
  const fields: [string, string][] = [
    ["correct", "Correct answer"],
    ["first_attempt_bonus", "Extra for a first-try answer"],
    ["year_bonus", "Bonus: knew the year"],
    ["actor_bonus", "Bonus: named an actor"],
    ["team_bonus", "Team bonus per correct answer"],
    ["hint_penalty", "Taken off per hint used"],
    ["incorrect_penalty", "Taken off per wrong guess"],
  ];
  return (
    <Card title="Scoring" footer={<SaveBar {...sec} />}>
      <div className="grid sm:grid-cols-3 gap-4">
        {fields.map(([k, label]) => <Num key={k} label={label} step={0.5} value={d[k]} onChange={(v) => sec.set(k, v ?? 0)} />)}
      </div>
      <Toggle checked={d.apply_difficulty_multiplier} onChange={(v) => sec.set("apply_difficulty_multiplier", v)} label="Multiply correct answers by the difficulty's points multiplier" />
      <p className="text-xs text-faint mt-2">New settings apply to games created afterwards. Running games keep the rules they started with.</p>
    </Card>
  );
}

/* ------------------------------------------------------------------ network */

interface Net { lan_enabled: boolean; lan_active: boolean; bound_host: string; port: number; addresses: string[]; join_urls: string[]; restart_required: boolean; devices: { role: string; label: string; ip: string | null; connected_for: number }[]; host_password_set: boolean }

function NetworkTab({ s, reload }: { s: SettingsResp; reload: () => Promise<void> }) {
  const net = useApi<Net>("/api/network");
  const { busy, run } = useAction();
  const toast = useToast();
  const [pw, setPw] = useState("");
  useInterval(() => void net.reload(), 5000);
  if (!net.data) return <Spinner />;
  const n = net.data;
  return (
    <>
      <Card title="Other devices">
        <p className="text-sm text-dim mb-3">
          Off by default: Movie Roulette only answers this computer. Turn it on to let phones, tablets and TVs on your home Wi-Fi join games.
          It is never opened to the internet.
        </p>
        <Toggle
          checked={s.sections.network.lan_enabled}
          onChange={(v) => run(async () => { await api.patch("/api/settings/network", { lan_enabled: v }); await reload(); await net.reload(); })}
          label="Allow devices on my network"
        />
        {n.restart_required && (
          <div className="rounded-lg border border-lamp/40 bg-lamp/5 p-3 text-sm mt-2 flex flex-wrap items-center gap-3">
            Restart Movie Roulette to apply this.
            <button
              className="btn btn-sm btn-lamp"
              onClick={() => run(async () => {
                await api.post("/api/system/restart");
                toast("Restarting. This page reconnects in a few seconds.");
                window.setTimeout(() => location.reload(), 4000);
              })}
            >
              Restart now
            </button>
          </div>
        )}
        <div className="mt-4 text-sm space-y-1">
          <StatusDot ok={n.lan_active} label={n.lan_active ? "Other devices can connect" : "Only this computer can connect"} />
          {n.lan_active && n.join_urls.map((u) => <p key={u} className="num">{u}</p>)}
          {n.lan_active && !n.join_urls.length && <p className="text-miss">No network address found. Is this computer connected to Wi-Fi or Ethernet?</p>}
        </div>
        {n.lan_active && (
          <p className="text-xs text-faint mt-3">
            If phones cannot connect, allow Python through Windows Defender Firewall for private networks (Windows usually asks the first time).
          </p>
        )}
      </Card>

      <Card title="Host password">
        <p className="text-sm text-dim mb-3">
          Needed to control games from a device other than this computer. Players never need it; they join with the game code.
          {s.host_password_set ? " A password is set." : " No password is set, so host controls only work on this computer."}
        </p>
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api.post("/api/settings/host-password", { new_password: pw }); setPw(""); await reload(); }, "Host password saved."); }}>
          <input className="field w-64" type="password" autoComplete="new-password" placeholder="At least 6 characters" value={pw} onChange={(e) => setPw(e.target.value)} aria-label="New host password" />
          <button className="btn btn-lamp" disabled={busy || pw.length < 6}>Set password</button>
          {s.host_password_set && <button type="button" className="btn btn-ghost" onClick={() => run(async () => { await api.post("/api/settings/host-password", { new_password: null }); await reload(); }, "Password removed.")}>Remove password</button>}
        </form>
      </Card>

      <Card title="Connected devices">
        {n.devices.length === 0 ? <p className="text-sm text-dim">No game screens are connected right now.</p> : (
          <ul className="text-sm space-y-1">
            {n.devices.map((d, i) => (
              <li key={i}>{d.label} <span className="text-dim">({d.role === "host" ? "host screen" : "player"}, {d.ip}, {Math.round(d.connected_for / 60)} min)</span></li>
            ))}
          </ul>
        )}
        <button className="btn btn-sm mt-3" onClick={() => run(async () => { await api.post("/api/auth/sessions/revoke-players"); }, "All players signed out.")}>Sign out all players</button>
      </Card>
    </>
  );
}

/* ------------------------------------------------------------------ database & appearance */

function DatabaseTab() {
  const backups = useApi<{ name: string; size: number; created: string }[]>("/api/database/backups");
  const info = useApi<{ version: string; python: string; ffmpeg: string | null; ffmpeg_path: string | null; data_dir: string; database: string }>("/api/system/info");
  const [check, setCheck] = useState<{ ok: boolean; integrity: string[]; foreign_key_problems: number; schema_version: string } | null>(null);
  const { busy, run } = useAction();
  return (
    <>
      <Card title="Backups">
        <p className="text-sm text-dim mb-3">Backups hold the library, categories, players and game history. Your movie files are not part of them.</p>
        <div className="flex flex-wrap gap-2 mb-4">
          <button className="btn btn-lamp" disabled={busy} onClick={() => run(async () => { await api.post("/api/database/backups"); await backups.reload(); }, "Backup created.")}>Back up now</button>
          <button className="btn" disabled={busy} onClick={() => run(async () => setCheck(await api.get("/api/database/integrity")))}>Check database</button>
        </div>
        {check && <p className={`text-sm mb-3 ${check.ok ? "text-hit" : "text-miss"}`}>{check.ok ? `Database is healthy (schema ${check.schema_version}).` : `Problems found: ${check.integrity.join("; ")}, ${check.foreign_key_problems} broken references.`}</p>}
        <ul className="divide-y divide-line/60 text-sm">
          {backups.data?.map((b) => (
            <li key={b.name} className="flex flex-wrap items-center gap-3 py-2">
              <span className="flex-1 num">{new Date(b.created).toLocaleString()}</span>
              <span className="text-dim">{(b.size / 1e6).toFixed(2)} MB</span>
              <button
                className="btn btn-sm"
                disabled={busy}
                onClick={() => confirm("Restore this backup? Everything changed since then is replaced (a safety backup is made first).") && run(async () => { await api.post("/api/database/restore", { name: b.name }); location.reload(); })}
              >
                Restore
              </button>
            </li>
          ))}
          {backups.data?.length === 0 && <li className="py-2 text-dim">No backups yet.</li>}
        </ul>
      </Card>
      <Card title="Played history">
        <p className="text-sm text-dim mb-3">Forget which movies were played, so repeat rules start fresh. Game history and statistics stay.</p>
        <button className="btn" disabled={busy} onClick={() => confirm("Reset played history?") && run(() => api.post("/api/settings/history/reset"), "Played history reset.")}>Reset played history</button>
      </Card>
      {info.data && (
        <Card title="About">
          <dl className="text-sm grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
            <dt className="text-dim">Version</dt><dd>{info.data.version}</dd>
            <dt className="text-dim">Python</dt><dd>{info.data.python}</dd>
            <dt className="text-dim">FFmpeg</dt><dd className="break-all">{info.data.ffmpeg ?? "Not found"}</dd>
            <dt className="text-dim">Data folder</dt><dd className="break-all">{info.data.data_dir}</dd>
          </dl>
        </Card>
      )}
    </>
  );
}

function AppearanceTab({ s, reload }: { s: SettingsResp; reload: () => Promise<void> }) {
  const sec = useSection(s, "appearance", reload);
  useEffect(() => {
    document.documentElement.dataset.theme = sec.draft.theme;
    document.documentElement.dataset.motion = sec.draft.animations ? "on" : "off";
  }, [sec.draft.theme, sec.draft.animations]);
  return (
    <Card title="Appearance" footer={<SaveBar {...sec} />}>
      <span className="lbl">Theme</span>
      <div className="flex gap-2 mb-3">
        {[["cinema", "Projection booth"], ["midnight", "Midnight"], ["noir", "Noir"]].map(([id, label]) => (
          <button key={id} className="chip" aria-pressed={sec.draft.theme === id} onClick={() => sec.set("theme", id)}>{label}</button>
        ))}
      </div>
      <Toggle checked={sec.draft.animations} onChange={(v) => sec.set("animations", v)} label="Animations" hint="The answer reveal and loading reels. Your system's reduce-motion setting is always respected." />
      <span className="lbl mt-3">Poster size in the library</span>
      <div className="flex gap-2 mb-2">
        {[["small", "Small"], ["medium", "Medium"], ["large", "Large"]].map(([id, label]) => (
          <button key={id} className="chip" aria-pressed={sec.draft.poster_size === id} onClick={() => sec.set("poster_size", id)}>{label}</button>
        ))}
      </div>
      <Toggle checked={sec.draft.show_ratings_in_library} onChange={(v) => sec.set("show_ratings_in_library", v)} label="Show TMDB ratings in the library" />
    </Card>
  );
}
