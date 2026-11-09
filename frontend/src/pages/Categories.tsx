import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowUp, Eye, EyeOff, Pencil, Plus, Trash2 } from "lucide-react";
import { api, type Category, type Facets } from "../lib/api";
import { useApi } from "../lib/hooks";
import { Empty, Modal, PageHeader, Spinner, Toggle, useAction } from "../components/ui";

const KIND_LABEL: Record<string, string> = {
  custom: "Your categories",
  genre: "Genres",
  decade: "Decades",
  collection: "Franchises",
  studio: "Studios",
};

export default function CategoriesPage() {
  const cats = useApi<Category[]>("/api/categories?include_empty=true");
  const { busy, run } = useAction();
  const [editing, setEditing] = useState<Category | "new" | null>(null);
  if (!cats.data) return <Spinner />;
  const list = cats.data;

  const move = (c: Category, dir: -1 | 1) => {
    const same = list.filter((x) => x.kind === c.kind);
    const i = same.findIndex((x) => x.id === c.id);
    const j = i + dir;
    if (j < 0 || j >= same.length) return;
    const ids = list.map((x) => x.id);
    const a = ids.indexOf(same[i].id);
    const b = ids.indexOf(same[j].id);
    [ids[a], ids[b]] = [ids[b], ids[a]];
    void run(async () => {
      await api.post("/api/categories/reorder", { ids });
      await cats.reload();
    });
  };

  return (
    <div className="max-w-5xl">
      <PageHeader
        title="Categories"
        sub="Genres, decades, franchises and studios come from TMDB automatically. Add your own for themed nights."
        actions={<button className="btn btn-lamp" onClick={() => setEditing("new")}><Plus className="size-4" /> New category</button>}
      />
      {list.length === 0 && <Empty title="No categories yet">Scan your library with a TMDB key and they appear here.</Empty>}
      <div className="space-y-8">
        {Object.entries(KIND_LABEL).map(([kind, label]) => {
          const group = list.filter((c) => c.kind === kind);
          if (!group.length) return null;
          return (
            <section key={kind}>
              <h2 className="title text-2xl mb-3">{label}</h2>
              <ul className="surface divide-y divide-line/60">
                {group.map((c, i) => (
                  <li key={c.id} className={`flex flex-wrap items-center gap-3 px-4 py-2.5 ${c.hidden ? "opacity-50" : ""}`}>
                    <Link to={`/library?category=${c.id}`} className="flex-1 min-w-0 hover:text-lamp">
                      {c.name}
                      {c.description && <span className="block text-xs text-dim truncate">{c.description}</span>}
                    </Link>
                    <span className="text-sm text-dim num w-20 text-right">{c.count} movie{c.count === 1 ? "" : "s"}</span>
                    <div className="flex gap-1">
                      <IconBtn label="Move up" disabled={busy || i === 0} onClick={() => move(c, -1)}><ArrowUp className="size-4" /></IconBtn>
                      <IconBtn label="Move down" disabled={busy || i === group.length - 1} onClick={() => move(c, 1)}><ArrowDown className="size-4" /></IconBtn>
                      <IconBtn label={c.hidden ? "Show in game setup" : "Hide from game setup"} disabled={busy} onClick={() => run(async () => { await api.patch(`/api/categories/${c.id}`, { hidden: !c.hidden }); await cats.reload(); })}>
                        {c.hidden ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                      </IconBtn>
                      <IconBtn label="Edit" onClick={() => setEditing(c)}><Pencil className="size-4" /></IconBtn>
                      {c.kind === "custom" && (
                        <IconBtn label="Delete" disabled={busy} onClick={() => confirm(`Delete "${c.name}"? Movies are not affected.`) && run(async () => { await api.del(`/api/categories/${c.id}`); await cats.reload(); })}>
                          <Trash2 className="size-4" />
                        </IconBtn>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>
      {editing && <CategoryEditor cat={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void cats.reload(); }} />}
    </div>
  );
}

function IconBtn({ label, children, ...rest }: { label: string; children: React.ReactNode; disabled?: boolean; onClick: () => unknown }) {
  return (
    <button className="btn btn-ghost btn-sm" aria-label={label} title={label} {...rest} onClick={() => void rest.onClick()}>
      {children}
    </button>
  );
}

function CategoryEditor({ cat, onClose, onSaved }: { cat: Category | null; onClose: () => void; onSaved: () => void }) {
  const facets = useApi<Facets>("/api/facets");
  const custom = !cat || cat.kind === "custom";
  const [name, setName] = useState(cat?.name ?? "");
  const [description, setDescription] = useState(cat?.description ?? "");
  const r = cat?.rules ?? {};
  const [genres, setGenres] = useState<number[]>(r.genres ?? []);
  const [decades, setDecades] = useState<number[]>(r.decades ?? []);
  const [collections, setCollections] = useState<number[]>(r.collections ?? []);
  const [studios, setStudios] = useState<number[]>(r.studios ?? []);
  const [unplayed, setUnplayed] = useState<boolean>(!!r.unplayed);
  const [minRating, setMinRating] = useState<string>(r.min_rating != null ? String(r.min_rating) : "");
  const [logic, setLogic] = useState<"all" | "any">(r.logic ?? "all");
  const { busy, run } = useAction();
  const toggle = (arr: number[], set: (v: number[]) => void, id: number) => set(arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id]);

  const save = () =>
    run(async () => {
      const rules = custom
        ? { logic, genres, decades, collections, studios, unplayed: unplayed || undefined, min_rating: minRating ? Number(minRating) : undefined }
        : undefined;
      if (cat) await api.patch(`/api/categories/${cat.id}`, { name, description, ...(rules ? { rules } : {}) });
      else await api.post("/api/categories", { name, description, rules });
      onSaved();
    }, "Category saved.");

  const chips = (items: { id: number; name: string }[] | undefined, sel: number[], set: (v: number[]) => void) => (
    <div className="flex flex-wrap gap-1.5">
      {(items ?? []).map((x) => (
        <button key={x.id} type="button" className="chip" aria-pressed={sel.includes(x.id)} onClick={() => toggle(sel, set, x.id)}>{x.name}</button>
      ))}
    </div>
  );

  return (
    <Modal open onClose={onClose} title={cat ? `Edit ${cat.name}` : "New category"} wide={custom}>
      <div className="space-y-4">
        <div>
          <label className="lbl" htmlFor="cn">Name</label>
          <input id="cn" className="field" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Halloween movies" />
        </div>
        <div>
          <label className="lbl" htmlFor="cd">Description (optional)</label>
          <input id="cd" className="field" value={description} maxLength={1000} onChange={(e) => setDescription(e.target.value)} />
        </div>
        {custom && (
          <>
            <p className="text-sm text-dim">
              Movies join this category when they match the rules below. You can also add single movies from the
              library, or leave the rules empty and pick every movie by hand.
            </p>
            <div><span className="lbl">Genres</span>{chips(facets.data?.genres, genres, setGenres)}</div>
            <div><span className="lbl">Decades</span>{chips(facets.data?.decades, decades, setDecades)}</div>
            <div><span className="lbl">Franchises</span>{chips(facets.data?.collections, collections, setCollections)}</div>
            <div><span className="lbl">Studios</span>{chips(facets.data?.studios.slice(0, 40), studios, setStudios)}</div>
            <div className="flex flex-wrap items-end gap-6">
              <div>
                <label className="lbl" htmlFor="mr">Minimum TMDB rating</label>
                <input id="mr" className="field w-24 num" inputMode="decimal" value={minRating} onChange={(e) => setMinRating(e.target.value)} placeholder="7.0" />
              </div>
              <Toggle checked={unplayed} onChange={setUnplayed} label="Only movies we haven't played" />
            </div>
            <fieldset className="flex gap-4 text-sm">
              <legend className="lbl">A movie must match</legend>
              <label className="flex items-center gap-2"><input type="radio" checked={logic === "all"} onChange={() => setLogic("all")} /> every rule</label>
              <label className="flex items-center gap-2"><input type="radio" checked={logic === "any"} onChange={() => setLogic("any")} /> any rule</label>
            </fieldset>
          </>
        )}
        <button className="btn btn-lamp" disabled={busy || !name.trim()} onClick={save}>Save category</button>
      </div>
    </Modal>
  );
}
