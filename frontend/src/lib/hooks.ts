import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type GameView, type Playback } from "./api";

/** Load data from the API with loading/error state and a reload function. */
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    const n = ++seq.current;
    setLoading(true);
    try {
      const d = await api.get<T>(path);
      if (n === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (n === seq.current) setError(e as ApiError);
    } finally {
      if (n === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, error, loading, reload: load, setData };
}

/** Re-run a callback every `ms` while `active`. */
export function useInterval(cb: () => void, ms: number, active = true) {
  const ref = useRef(cb);
  useEffect(() => {
    ref.current = cb;
  }, [cb]);
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => ref.current(), ms);
    return () => window.clearInterval(id);
  }, [ms, active]);
}

export type SocketStatus = "connecting" | "open" | "closed";

/**
 * Live game state over WebSocket. The server always sends a full, role-filtered snapshot, so reconnecting
 * (Wi-Fi drop, phone sleep, server restart) needs nothing more than opening the socket again.
 * Also estimates the server clock offset so playback positions line up across devices.
 */
export function useGameSocket(gameId: number | null, role: "host" | "player") {
  const [state, setState] = useState<GameView | null>(null);
  const [status, setStatus] = useState<SocketStatus>("connecting");
  const [authFailed, setAuthFailed] = useState(false);
  const offset = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (gameId === null) return;
    let stopped = false;
    let retry = 0;
    let timer: number | undefined;
    let pinger: number | undefined;

    const connect = () => {
      if (stopped) return;
      setStatus("connecting");
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws/game/${gameId}?role=${role}`);
      wsRef.current = ws;
      ws.onopen = () => {
        retry = 0;
        setStatus("open");
        const ping = () => ws.readyState === 1 && ws.send(JSON.stringify({ type: "ping", t: Date.now() / 1000 }));
        ping();
        pinger = window.setInterval(ping, 15000);
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "state") setState(msg.state);
          if (msg.type === "pong" && typeof msg.t === "number") {
            const now = Date.now() / 1000;
            offset.current = msg.server_time - (msg.t + now) / 2;
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = (ev) => {
        window.clearInterval(pinger);
        setStatus("closed");
        if (ev.code === 4401 || ev.code === 4403) {
          setAuthFailed(true);
          return;
        }
        if (stopped) return;
        retry = Math.min(retry + 1, 6);
        timer = window.setTimeout(connect, 500 * 2 ** (retry - 1));
      };
      ws.onerror = () => ws.close();
    };
    connect();
    const onVisible = () => {
      if (document.visibilityState === "visible" && wsRef.current?.readyState === 1) {
        wsRef.current.send(JSON.stringify({ type: "sync" }));
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.clearInterval(pinger);
      document.removeEventListener("visibilitychange", onVisible);
      wsRef.current?.close();
    };
  }, [gameId, role]);

  return { state, setState, status, authFailed, serverOffset: offset };
}

/** Current playback position (seconds into the clip) computed from the synced state. */
export function playbackPosition(pb: Playback | null | undefined, offsetSec: number): number {
  if (!pb) return 0;
  let pos = pb.position_base;
  if (pb.state === "playing" && pb.started_at) {
    const serverNow = Date.now() / 1000 + offsetSec;
    pos += Math.max(0, serverNow - pb.started_at);
  }
  return pb.length ? Math.min(pos, pb.length) : pos;
}
