import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, Ban, Check, RefreshCw, Search, Star } from "lucide-react";
import { api, fmtTime, qs, type Category, type Facets, type MovieCard } from "../lib/api";
import { useApi } from "../lib/hooks";
import { Empty, ErrorNote, Modal, PageHeader, Poster, Spinner, Toggle, useAction } from "../components/ui";

interface Detail extends MovieCard {
  tmdb_id: number | null;
  original_title: string | null;
  overview: string | null;
  runtime: number | null;
  backdrop_path: string | null;
  match_source: string | null;
  confirmed: boolean;
  keywords: string[];
  genres_full?: never;
  collection: { id: number; name: string } | null;
  directors: { id: number; name: string }[];
  cast: { id: number; name: string; character: string | null }[];
  studios: { id: number; name: string }[];
  files: { id: number; path: string; filename: string; size: number; duration: number | null; container: string | null;
    video_codec: string | null; audio_codec: string | null; width: number | null; height: number | null; status: string;
    error: string | null; duplicate_of_id: number | null }[];
  categories: { id: number; name: string; kind: string }[];
  excluded_categories: { id: number; name: string; kind: string }[];
  candidates: Candidate[];
}
interface Candidate { tmdb_id: number; title: string; original_title: string | null; release_year: number | null; overview: string | null; poster_path: string | null; score: number; in_library?: number | null }

const STATUS_LABEL: Record<string, string> = { matched: "Matched", needs_review: "Needs a match check", unmatched: "Not matched yet", failed: "TMDB lookup failed" };

export default function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const facets = useApi<Facets>("/api/facets");
  const look = useApi<{ sections: { appearance: { poster_size: string; show_ratings_in_library: boolean } } }>("/api/settings");
  const showRatings = look.data?.sections.appearance.show_ratings_in_library ?? true;
  const gridCols = { small: "grid-cols-3 sm:grid-cols-5 lg:grid-cols-8", medium: "grid-cols-2 sm:grid-cols-4 lg:grid-cols-6", large: "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4" }[look.data?.sections.appearance.poster_size ?? "medium"] ?? "grid-cols-2 sm:grid-cols-4 lg:grid-cols-6";
  const [q, setQ] = useState(params.get("q") ?? "");
  const [page, setPage] = useState(1);
  const filter = {
    status: params.get("status") ?? "",
    genre: params.get("genre") ?? "",
    decade: params.get("decade") ?? "",
    collection: params.get("collection") ?? "",
    studio: params.get("studio") ?? "",
    person: params.get("person") ?? "",
    category: params.get("category") ?? "",
    sort: params.get("sort") ?? "title",
    order: params.get("order") ?? "asc",
  };
  const [debounced, setDebounced] = useState(q);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  useEffect(() => setPage(1), [debounced, params]);
  const path = `/api/movies${qs({ q: debounced, ...filter, page, page_size: 60 })}`;
  const list = useApi<{ total: number; items: MovieCard[] }>(path);
  const openId = params.get("movie");

  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    p.delete("movie");
    setParams(p, { replace: true });
  };
  const open = (id: number | null) => {
    const p = new URLSearchParams(params);
    if (id) p.set("movie", String(id));
    else p.delete("movie");
    setParams(p, { replace: true });
  };
  const personLabel = useMemo(() => params.get("person_name"), [params]);

  return (
    <div className="max-w-7xl">
      <PageHeader
        title="Movie library"
        sub={list.data ? `${list.data.total} movie${list.data.total === 1 ? "" : "s"} shown${filter.category ? " in this category" : ""}` : undefined}
        actions={filter.category ? <button className="btn btn-sm" onClick={() => set("category", "")}>Show all categories</button> : undefined}
      />
      <div className="flex flex-wrap gap-2 mb-5 items-end">
        <div className="w-full sm:w-72">
          <label className="lbl" htmlFor="lib-search">Search</label>
          <div className="relative">
            <Search className="size-4 absolute left-3 top-3 text-faint" aria-hidden />
            <input id="lib-search" className="field pl-9" placeholder="Title" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
        <Select label="Status" value={filter.status} onChange={(v) => set("status", v)} options={[["", "All"], ["matched", "Matched"], ["needs_review", "Needs a match check"], ["unmatched", "Not matched"], ["failed", "Lookup failed"], ["missing", "File missing"]]} />
        <Select label="Genre" value={filter.genre} onChange={(v) => set("genre", v)} options={[["", "Any genre"], ...(facets.data?.genres.map((g) => [String(g.id), g.name] as [string, string]) ?? [])]} />
        <Select label="Decade" value={filter.decade} onChange={(v) => set("decade", v)} options={[["", "Any decade"], ...(facets.data?.decades.map((d) => [String(d.id), d.name] as [string, string]) ?? [])]} />
        <Select label="Franchise" value={filter.collection} onChange={(v) => set("collection", v)} options={[["", "Any franchise"], ...(facets.data?.collections.map((c) => [String(c.id), c.name] as [string, string]) ?? [])]} />
        <Select label="Studio" value={filter.studio} onChange={(v) => set("studio", v)} options={[["", "Any studio"], ...(facets.data?.studios.map((c) => [String(c.id), c.name] as [string, string]) ?? [])]} />
        <PersonFilter value={filter.person} label={personLabel} onChange={(id, name) => {
          const p = new URLSearchParams(params);
          if (id) { p.set("person", String(id)); p.set("person_name", name); } else { p.delete("person"); p.delete("person_name"); }
          setParams(p, { replace: true });
        }} />
        <Select label="Sort by" value={`${filter.sort}:${filter.order}`} onChange={(v) => {
          const [s, o] = v.split(":");
          const p = new URLSearchParams(params);
          p.set("sort", s);
          p.set("order", o);
          setParams(p, { replace: true });
        }} options={[["title:asc", "Title"], ["year:desc", "Newest"], ["year:asc", "Oldest"], ["added:desc", "Recently added"], ["rating:desc", "TMDB rating"], ["runtime:desc", "Longest"]]} />
      </div>

      <ErrorNote error={list.error} />
      {!list.data ? (
        <Spinner />
      ) : list.data.items.length === 0 ? (
        <Empty title="No movies here">Change the filters, or add a movie folder in Settings and scan it.</Empty>
      ) : (
        <>
          <ul className={`grid ${gridCols} gap-x-4 gap-y-6`}>
            {list.data.items.map((m) => (
              <li key={m.id}>
                <button className="w-full text-left group" onClick={() => open(m.id)}>
                  <div className="relative">
                    <Poster path={m.poster_path} title={m.title} className={`transition-opacity ${m.excluded_from_game || !m.playable ? "opacity-40" : "group-hover:opacity-90"}`} />
                    {m.metadata_status !== "matched" && (
                      <span className="absolute top-2 left-2 rounded bg-black/80 px-2 py-0.5 text-[11px] text-lamp">{STATUS_LABEL[m.metadata_status]}</span>
                    )}
                    {m.excluded_from_game && <span className="absolute top-2 right-2 rounded bg-black/80 px-2 py-0.5 text-[11px]">Not in games</span>}
                    {!m.playable && <span className="absolute bottom-2 left-2 rounded bg-black/80 px-2 py-0.5 text-[11px] text-miss">File missing</span>}
                  </div>
                  <p className="mt-2 text-sm leading-snug group-hover:text-lamp">{m.title}</p>
                  <p className="text-xs text-dim flex items-center gap-2">
                    {m.release_year ?? (m.parsed_year ? `${m.parsed_year}?` : "")}
                    {showRatings && m.vote_average ? <span className="inline-flex items-center gap-0.5"><Star className="size-3" aria-hidden />{m.vote_average.toFixed(1)}</span> : null}
                  </p>
                </button>
              </li>
            ))}
          </ul>
          {list.data.total > 60 && (
            <div className="flex items-center gap-3 mt-8">
              <button className="btn btn-sm" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button>
              <span className="text-sm text-dim num">Page {page} of {Math.ceil(list.data.total / 60)}</span>
              <button className="btn btn-sm" disabled={page * 60 >= list.data.total} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          )}
        </>
      )}
      {openId && <MovieModal id={Number(openId)} onClose={() => open(null)} onChanged={(id) => { void list.reload(); void facets.reload(); if (id !== Number(openId)) open(id); }} />}
    </div>
  );
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="block">
      <span className="lbl">{label}</span>
      <select className="field w-auto max-w-[11rem] py-2 text-sm" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

function PersonFilter({ value, label, onChange }: { value: string; label: string | null; onChange: (id: number | null, name: string) => void }) {
  const [q, setQ] = useState("");
  const [opts, setOpts] = useState<{ id: number; name: string; role: string; count: number }[]>([]);
  useEffect(() => {
    if (!q) return setOpts([]);
    const t = window.setTimeout(() => api.get<typeof opts>(`/api/people?q=${encodeURIComponent(q)}&limit=8`).then(setOpts).catch(() => {}), 200);
    return () => window.clearTimeout(t);
  }, [q]);
  if (value) return (
    <div>
      <span className="lbl">Actor or director</span>
      <button className="chip" aria-pressed onClick={() => onChange(null, "")}>{label ?? "Selected person"} <span aria-hidden>×</span><span className="sr-only">Remove</span></button>
    </div>
  );
  return (
    <div className="relative">
      <label className="lbl" htmlFor="pf">Actor or director</label>
      <input id="pf" className="field w-48" placeholder="Search people" value={q} onChange={(e) => setQ(e.target.value)} />
      {opts.length > 0 && (
        <ul className="absolute z-20 mt-1 w-64 surface p-1 shadow-2xl">
          {opts.map((o) => (
            <li key={`${o.id}${o.role}`}>
              <button className="w-full text-left px-3 py-1.5 rounded hover:bg-panel-2 text-sm" onClick={() => { onChange(o.id, o.name); setQ(""); }}>
                {o.name} <span className="text-faint text-xs">{o.role === "director" ? "director" : "actor"}, {o.count}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MovieModal({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: (newId: number) => void }) {
  const d = useApi<Detail>(`/api/movies/${id}`);
  const { busy, run } = useAction();
  const [matchOpen, setMatchOpen] = useState(false);
  const m = d.data;
  return (
    <Modal open onClose={onClose} title={m ? m.title : "Movie"} wide>
      {!m ? <Spinner /> : (
        <div className="space-y-5">
          <div className="flex gap-5">
            <Poster path={m.poster_path} title={m.title} className="w-32 shrink-0" />
            <div className="min-w-0 space-y-2 text-sm">
              <p className="text-dim">
                {[m.release_year, m.runtime ? `${m.runtime} min` : null, m.original_title !== m.title ? m.original_title : null].filter(Boolean).join(", ")}
              </p>
              {m.overview && <p className="max-w-prose">{m.overview}</p>}
              {m.directors.length > 0 && <p><span className="text-dim">Directed by</span> {m.directors.map((x) => x.name).join(", ")}</p>}
              {m.cast.length > 0 && <p><span className="text-dim">Cast</span> {m.cast.slice(0, 6).map((x) => x.name).join(", ")}</p>}
              {m.collection && <p><span className="text-dim">Franchise</span> {m.collection.name}</p>}
              {m.studios.length > 0 && <p><span className="text-dim">Studios</span> {m.studios.map((s) => s.name).join(", ")}</p>}
              <p className="text-dim">
                {STATUS_LABEL[m.metadata_status]}
                {m.match_source && `, ${m.match_source === "manual" ? "confirmed by you" : `matched automatically (${Math.round(m.match_confidence ?? 0)}% confidence)`}`}
              </p>
            </div>
          </div>

          {m.metadata_status !== "matched" && (
            <div className="rounded-lg border border-lamp/40 bg-lamp/5 p-4 text-sm flex flex-wrap items-center justify-between gap-3">
              <span>
                Movie Roulette was not sure which film <b>{m.files[0]?.filename}</b> is. It stays out of games until you confirm it.
              </span>
              <button className="btn btn-lamp btn-sm" onClick={() => setMatchOpen(true)}>Choose the right movie</button>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Toggle
              checked={!m.excluded_from_game}
              disabled={busy}
              onChange={(v) => run(async () => { await api.patch(`/api/movies/${m.id}`, { excluded_from_game: !v }); await d.reload(); onChanged(m.id); })}
              label="Can come up in games"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            {m.metadata_status === "matched" && (
              <>
                <button className="btn btn-sm" onClick={() => setMatchOpen(true)}>Change the match</button>
                <button className="btn btn-sm" disabled={busy} onClick={() => run(async () => { await api.post(`/api/movies/${m.id}/refresh`); await d.reload(); }, "Metadata refreshed.")}>
                  <RefreshCw className="size-3.5" /> Refresh from TMDB
                </button>
                <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => run(async () => { await api.post(`/api/movies/${m.id}/unmatch`); await d.reload(); onChanged(m.id); })}>
                  <Ban className="size-3.5" /> Remove match
                </button>
              </>
            )}
          </div>

          {m.categories.length > 0 && (
            <div>
              <h3 className="text-sm text-dim mb-1.5">In categories</h3>
              <div className="flex flex-wrap gap-1.5">
                {m.categories.map((c) => (
                  <span key={c.id} className="chip cursor-default">
                    {c.name}
                    <button
                      className="text-faint hover:text-miss"
                      title={`Keep this movie out of ${c.name}`}
                      aria-label={`Remove from ${c.name}`}
                      onClick={() => run(async () => { await api.post(`/api/categories/${c.id}/movies`, { movie_ids: [m.id], mode: "exclude" }); await d.reload(); })}
                    >×</button>
                  </span>
                ))}
              </div>
              {m.excluded_categories.length > 0 && (
                <p className="text-xs text-dim mt-2">
                  Kept out of: {m.excluded_categories.map((c) => (
                    <button key={c.id} className="underline mr-2" onClick={() => run(async () => { await api.post(`/api/categories/${c.id}/movies`, { movie_ids: [m.id], mode: "clear" }); await d.reload(); })}>{c.name} (undo)</button>
                  ))}
                </p>
              )}
            </div>
          )}

          <AddToCategory movieId={m.id} member={m.categories.map((c) => c.id)} onDone={() => void d.reload()} />

          <div>
            <h3 className="text-sm text-dim mb-1.5">Files</h3>
            <ul className="space-y-2 text-sm">
              {m.files.map((f) => (
                <li key={f.id} className="rounded-lg bg-house px-3 py-2">
                  <p className="break-all">{f.path}</p>
                  <p className="text-xs text-dim mt-0.5">
                    {f.status === "ok" ? <Check className="inline size-3 text-hit" /> : <AlertTriangle className="inline size-3 text-miss" />}{" "}
                    {[f.status !== "ok" ? f.error || f.status : null, fmtTime(f.duration), f.width && `${f.width}x${f.height}`, f.video_codec, f.audio_codec, `${(f.size / 1e9).toFixed(2)} GB`, f.duplicate_of_id ? "identical copy" : null].filter(Boolean).join(", ")}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
      {m && matchOpen && (
        <MatchModal movie={m} onClose={() => setMatchOpen(false)} onMatched={(newId) => { setMatchOpen(false); void d.reload(); onChanged(newId); }} />
      )}
    </Modal>
  );
}

function AddToCategory({ movieId, member, onDone }: { movieId: number; member: number[]; onDone: () => void }) {
  const cats = useApi<Category[]>("/api/categories");
  const [sel, setSel] = useState("");
  const { busy, run } = useAction();
  const custom = (cats.data ?? []).filter((c) => c.kind === "custom" && !member.includes(c.id));
  if (!custom.length) return null;
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div>
        <label className="lbl" htmlFor="atc">Add to one of your categories</label>
        <select id="atc" className="field" value={sel} onChange={(e) => setSel(e.target.value)}>
          <option value="">Choose a category</option>
          {custom.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <button
        className="btn btn-sm"
        disabled={busy || !sel}
        onClick={() => run(async () => {
          await api.post(`/api/categories/${sel}/movies`, { movie_ids: [movieId], mode: "include" });
          setSel("");
          onDone();
          await cats.reload();
        }, "Added to the category.")}
      >
        Add
      </button>
    </div>
  );
}

function MatchModal({ movie, onClose, onMatched }: { movie: Detail; onClose: () => void; onMatched: (id: number) => void }) {
  const [query, setQuery] = useState(movie.parsed_title ?? movie.title);
  const [year, setYear] = useState(movie.parsed_year ? String(movie.parsed_year) : "");
  const [results, setResults] = useState<Candidate[]>(movie.candidates);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title="Choose the right movie" wide>
      <p className="text-sm text-dim mb-3 break-all">File: {movie.files[0]?.filename} ({fmtTime(movie.files[0]?.duration)})</p>
      <form
        className="flex flex-wrap gap-2 mb-4"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => setResults(await api.post<Candidate[]>(`/api/movies/${movie.id}/match/search`, { query, year: year ? Number(year) : null })));
        }}
      >
        <input className="field flex-1 min-w-48" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Title" />
        <input className="field w-24 num" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="Year" aria-label="Year" />
        <button className="btn" disabled={busy || !query.trim()}><Search className="size-4" /> Search TMDB</button>
      </form>
      {results.length === 0 ? (
        <p className="text-sm text-dim">No TMDB results yet. Search with the movie's title.</p>
      ) : (
        <ul className="grid sm:grid-cols-2 gap-3">
          {results.map((c) => (
            <li key={c.tmdb_id} className="flex gap-3 rounded-lg border border-line p-3">
              <Poster path={c.poster_path} title={c.title} size="w154" className="w-16 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{c.title} {c.release_year && <span className="text-dim font-normal">({c.release_year})</span>}</p>
                <p className="text-xs text-dim line-clamp-2">{c.overview}</p>
                <p className="text-xs text-faint mt-1">Match score {Math.round(c.score)}{c.in_library && c.in_library !== movie.id ? ", already in your library (files will be grouped)" : ""}</p>
                <button
                  className="btn btn-lamp btn-sm mt-2"
                  disabled={busy}
                  onClick={() => run(async () => {
                    const r = await api.post<{ id: number }>(`/api/movies/${movie.id}/match`, { tmdb_id: c.tmdb_id });
                    onMatched(r.id);
                  }, "Match saved.")}
                >
                  This is it
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
