import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TimestampPicker from "../components/TimestampPicker";
import { RevealCard, Scoreboard } from "../components/GameBits";
import { fmtTime, parseTime, qs, type GameView, type RoundView } from "../lib/api";
import { playbackPosition } from "../lib/hooks";

function round(over: Partial<RoundView> = {}): RoundView {
  return {
    token: "tok", number: 1, status: "timestamp", category_label: "Comedy", difficulty: "medium",
    turn_player_id: 1, turn_player: "Ana", turn_team_id: null, window: { min: 100, max: 5000 },
    clip_seconds: 30, max_seconds: null, mode: "clip", start_seconds: null, timestamp_source: null,
    suggested_seconds: null, can_pick_timestamp: true, timestamp_options: { manual: true, random: true },
    hints: [], hints_left: 1, replays_left: 1, playback: null, outcome: null, guesses: [], revealed: false,
    answer: null, ...over,
  };
}

describe("time helpers", () => {
  it("formats and parses", () => {
    expect(fmtTime(3723)).toBe("1:02:03");
    expect(fmtTime(null)).toBe("--:--");
    expect(parseTime("1:02:03")).toBe(3723);
    expect(parseTime("62:03")).toBe(3723);
    expect(parseTime("90")).toBe(90);
    expect(parseTime("1:99")).toBeNull();
    expect(parseTime("abc")).toBeNull();
    expect(qs({ a: 1, b: "", c: null, d: "x y" })).toBe("?a=1&d=x+y");
  });

  it("computes the synced playback position", () => {
    const now = Date.now() / 1000;
    const pb = { state: "playing", position_base: 2, started_at: now - 3, length: 30 } as never;
    expect(playbackPosition(pb, 0)).toBeGreaterThan(4.9);
    expect(playbackPosition({ ...(pb as object), started_at: now - 100 } as never, 0)).toBe(30);
    expect(playbackPosition({ state: "paused", position_base: 7, started_at: null, length: 30 } as never, 0)).toBe(7);
  });
});

describe("TimestampPicker", () => {
  it("only confirms times inside the allowed window", async () => {
    const onConfirm = vi.fn();
    render(<TimestampPicker round={round()} busy={false} onRandom={vi.fn()} onConfirm={onConfirm} onClearSuggestion={vi.fn()} />);
    const text = screen.getByLabelText("Or type a time");
    await userEvent.clear(text);
    await userEvent.type(text, "9:00:00");
    expect(screen.getByText(/Use a time between 0:01:40 and 1:23:20/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start at/ })).toBeDisabled();
    await userEvent.clear(text);
    await userEvent.type(text, "0:20:00");
    const go = screen.getByRole("button", { name: "Start at 0:20:00" });
    expect(go).toBeEnabled();
    await userEvent.click(go);
    expect(onConfirm).toHaveBeenCalledWith(1200, "manual");
  });

  it("random mode: generate, then accept the suggestion", async () => {
    const onRandom = vi.fn();
    const onConfirm = vi.fn();
    const { rerender } = render(<TimestampPicker round={round({ timestamp_options: { manual: false, random: true } })} busy={false} onRandom={onRandom} onConfirm={onConfirm} onClearSuggestion={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Use this time" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Generate a random time" }));
    expect(onRandom).toHaveBeenCalled();
    rerender(<TimestampPicker round={round({ timestamp_options: { manual: false, random: true }, suggested_seconds: 777 })} busy={false} onRandom={onRandom} onConfirm={onConfirm} onClearSuggestion={vi.fn()} />);
    expect(screen.getByText("0:12:57")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use this time" }));
    expect(onConfirm).toHaveBeenCalledWith(777, "random");
    expect(screen.queryByRole("button", { name: "Pick a time instead" })).toBeNull();
  });
});

describe("game pieces", () => {
  it("shows tied ranks", () => {
    const game = {
      team_mode: false, players: [{ id: 1, online: true }, { id: 2, online: false }],
      scoreboard: { teams: [], players: [
        { kind: "player", id: 1, name: "Ana", color: "#fff", points: 10, correct: 1, rank: 1, tied: true },
        { kind: "player", id: 2, name: "Ben", color: "#fff", points: 10, correct: 1, rank: 1, tied: true },
      ] },
    } as unknown as GameView;
    render(<Scoreboard game={game} />);
    expect(screen.getAllByText("1=")).toHaveLength(2);
  });

  it("reveal card shows the answer details", () => {
    render(<RevealCard outcome="correct" answer={{ title: "Alien", original_title: "Alien", release_year: 1979, poster_path: null, backdrop_path: null, vote_average: 8.1, genres: ["Horror"], directors: ["Ridley Scott"], cast: [], overview: null, tmdb_id: 348, runtime: 117, collection: null, start_seconds: 600 }} />);
    expect(screen.getByRole("heading", { name: "Alien" })).toBeInTheDocument();
    expect(screen.getByText(/Directed by Ridley Scott/)).toBeInTheDocument();
    expect(screen.getByText("Scene at 0:10:00")).toBeInTheDocument();
    expect(screen.getByText("8.1 on TMDB")).toBeInTheDocument();
  });
});
