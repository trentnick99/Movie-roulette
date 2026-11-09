import { useEffect, useRef, useState } from "react";
import { Check, Dices, RotateCcw } from "lucide-react";
import { fmtTime, parseTime, type RoundView } from "../lib/api";

/**
 * Choose where the scene starts: drag along the film strip, use the slider, type h/m/s or a full time, or ask
 * for a random start. Only times inside the allowed window can be confirmed.
 */
export default function TimestampPicker({
  round,
  duration,
  busy,
  onRandom,
  onConfirm,
  onClearSuggestion,
}: {
  round: RoundView;
  duration?: number | null;
  busy: boolean;
  onRandom: () => void;
  onConfirm: (seconds: number, source: "manual" | "random") => void;
  onClearSuggestion: () => void;
}) {
  const w = round.window ?? { min: 0, max: 0 };
  const span = Math.max(w.max + (round.clip_seconds ?? 30), duration ?? 0, 1);
  const manualAllowed = round.timestamp_options.manual;
  const randomAllowed = round.timestamp_options.random;
  const suggestion = round.suggested_seconds;
  const [mode, setMode] = useState<"manual" | "random">(manualAllowed ? "manual" : "random");
  const [value, setValue] = useState<number>(Math.round((w.min + w.max) / 2));
  const [text, setText] = useState(fmtTime(value));
  const strip = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  useEffect(() => {
    if (suggestion !== null && suggestion !== undefined) {
      setMode("random");
    }
  }, [suggestion]);

  const clamp = (s: number) => Math.min(w.max, Math.max(w.min, Math.round(s)));
  const set = (s: number) => {
    const c = clamp(s);
    setValue(c);
    setText(fmtTime(c));
  };
  const hms = { h: Math.floor(value / 3600), m: Math.floor((value % 3600) / 60), s: value % 60 };
  const setPart = (part: "h" | "m" | "s", n: number) => {
    const next = { ...hms, [part]: Math.max(0, Number.isNaN(n) ? 0 : n) };
    set(next.h * 3600 + Math.min(59, next.m) * 60 + Math.min(59, next.s));
  };
  const fromPointer = (clientX: number) => {
    const r = strip.current?.getBoundingClientRect();
    if (!r) return;
    set(((clientX - r.left) / r.width) * span);
  };
  const typed = parseTime(text);
  const typedValid = typed !== null && typed >= w.min && typed <= w.max;
  const shown = mode === "random" && suggestion != null ? suggestion : value;

  return (
    <div className="space-y-4">
      {manualAllowed && randomAllowed && (
        <div className="flex gap-2" role="tablist" aria-label="How to choose the start">
          <button role="tab" aria-selected={mode === "manual"} className="chip" aria-pressed={mode === "manual"} onClick={() => setMode("manual")}>
            Pick a time
          </button>
          <button role="tab" aria-selected={mode === "random"} className="chip" aria-pressed={mode === "random"} onClick={() => setMode("random")}>
            Random time
          </button>
        </div>
      )}

      <div>
        <div
          ref={strip}
          className="filmstrip"
          onPointerDown={(e) => {
            if (mode !== "manual") return;
            dragging.current = true;
            (e.target as Element).setPointerCapture?.(e.pointerId);
            fromPointer(e.clientX);
          }}
          onPointerMove={(e) => dragging.current && fromPointer(e.clientX)}
          onPointerUp={() => (dragging.current = false)}
          aria-hidden
        >
          <div className="frames" />
          <div className="allowed" style={{ left: `${(w.min / span) * 100}%`, width: `${((w.max - w.min) / span) * 100}%` }} />
          <div className="gate" style={{ left: `${(shown / span) * 100}%` }} />
        </div>
        <div className="flex justify-between text-xs text-faint mt-1.5 num">
          <span>Allowed from {fmtTime(w.min)}</span>
          <span>to {fmtTime(w.max)}</span>
        </div>
      </div>

      {mode === "manual" ? (
        <div className="space-y-3">
          <input
            type="range"
            min={w.min}
            max={w.max}
            step={1}
            value={value}
            onChange={(e) => set(Number(e.target.value))}
            className="w-full accent-[var(--color-lamp)]"
            aria-label="Start time"
          />
          <div className="flex flex-wrap items-end gap-3">
            {(["h", "m", "s"] as const).map((p) => (
              <div key={p} className="w-20">
                <label className="lbl" htmlFor={`ts-${p}`}>{p === "h" ? "Hours" : p === "m" ? "Minutes" : "Seconds"}</label>
                <input
                  id={`ts-${p}`}
                  className="field num"
                  type="number"
                  min={0}
                  max={p === "h" ? 9 : 59}
                  value={hms[p]}
                  onChange={(e) => setPart(p, Number(e.target.value))}
                />
              </div>
            ))}
            <div className="w-36">
              <label className="lbl" htmlFor="ts-text">Or type a time</label>
              <input
                id="ts-text"
                className={`field num ${text && !typedValid ? "border-miss" : ""}`}
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  const t = parseTime(e.target.value);
                  if (t !== null && t >= w.min && t <= w.max) setValue(Math.round(t));
                }}
                placeholder="1:05:30"
                aria-invalid={!!text && !typedValid}
              />
            </div>
          </div>
          {text && !typedValid && <p className="text-xs text-miss">Use a time between {fmtTime(w.min)} and {fmtTime(w.max)}.</p>}
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-lamp" disabled={busy || !typedValid} onClick={() => onConfirm(value, "manual")}>
              <Check className="size-4" /> Start at {fmtTime(value)}
            </button>
            <button className="btn btn-ghost" onClick={() => set(Math.round((w.min + w.max) / 2))}>
              <RotateCcw className="size-4" /> Clear
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="num title text-4xl">{suggestion != null ? fmtTime(suggestion) : "--:--:--"}</p>
          <div className="flex flex-wrap gap-2">
            <button className="btn" disabled={busy} onClick={onRandom}>
              <Dices className="size-4" /> {suggestion != null ? "Another random time" : "Generate a random time"}
            </button>
            <button
              className="btn btn-lamp"
              disabled={busy || suggestion == null}
              onClick={() => suggestion != null && onConfirm(suggestion, "random")}
            >
              <Check className="size-4" /> Use this time
            </button>
            {manualAllowed && (
              <button
                className="btn btn-ghost"
                onClick={() => {
                  setMode("manual");
                  onClearSuggestion();
                }}
              >
                Pick a time instead
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
