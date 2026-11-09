import { useEffect, useRef, useState, type MutableRefObject } from "react";
import Hls from "hls.js";
import { AlertTriangle, Maximize, Minimize, Volume2, VolumeX } from "lucide-react";
import type { Playback } from "../lib/api";
import { fmtTime } from "../lib/api";
import { playbackPosition } from "../lib/hooks";

/**
 * Plays the round's clip and keeps it in step with the host's playback state (shared server clock).
 * The element carries no title, file name or metadata - only the anonymous round stream.
 */
export default function VideoStage({
  playback,
  offset,
  defaultVolume = 0.8,
  startMuted = false,
  showControlsNote,
}: {
  playback: Playback | null;
  offset: MutableRefObject<number>;
  defaultVolume?: number;
  startMuted?: boolean;
  showControlsNote?: string;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const [muted, setMuted] = useState(startMuted);
  const [volume, setVolume] = useState(defaultVolume);
  const [buffering, setBuffering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsTap, setNeedsTap] = useState(false);
  const [pos, setPos] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const url = playback?.media_status === "ready" ? playback.stream_url : null;
  const pbRef = useRef(playback);
  pbRef.current = playback;

  // attach the stream
  useEffect(() => {
    const v = video.current;
    setError(null);
    if (!v || !url) return;
    if (Hls.isSupported()) {
      const hls = new Hls({ enableWorker: true, lowLatencyMode: false, backBufferLength: 120 });
      hlsRef.current = hls;
      let recovered = 0;
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (!data.fatal) return;
        if (recovered < 3) {
          recovered += 1;
          if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
          else hls.startLoad();
        } else {
          setError("The video stream was interrupted. Check the connection to the host computer.");
        }
      });
      hls.loadSource(url);
      hls.attachMedia(v);
      return () => {
        hls.destroy();
        hlsRef.current = null;
      };
    }
    if (v.canPlayType("application/vnd.apple.mpegurl")) {
      v.src = url; // Safari / iOS play HLS natively
      return () => {
        v.removeAttribute("src");
        v.load();
      };
    }
    setError("This browser cannot play video streams. Try Chrome, Edge, Firefox or Safari.");
  }, [url]);

  useEffect(() => {
    const v = video.current;
    if (v) {
      v.volume = volume;
      v.muted = muted;
    }
  }, [volume, muted]);

  // follow the shared playback state
  useEffect(() => {
    const tick = () => {
      const v = video.current;
      const pb = pbRef.current;
      if (!v || !pb) return;
      const desired = playbackPosition(pb, offset.current);
      setPos(desired);
      if (!url || v.readyState < 1) return;
      const atEnd = pb.length !== null && desired >= pb.length - 0.05;
      if (pb.state === "playing" && !atEnd) {
        if (Math.abs(v.currentTime - desired) > 0.8) v.currentTime = desired;
        if (v.paused) {
          v.play().catch(() => {
            // autoplay with sound blocked: play muted, offer a tap for sound
            v.muted = true;
            setMuted(true);
            setNeedsTap(true);
            v.play().catch(() => setNeedsTap(true));
          });
        }
      } else {
        if (!v.paused) v.pause();
        const target = atEnd ? Math.max(0, (pb.length ?? desired) - 0.05) : desired;
        if (Math.abs(v.currentTime - target) > 0.3) v.currentTime = target;
      }
    };
    tick();
    const id = window.setInterval(tick, 250);
    return () => window.clearInterval(id);
  }, [url, offset]);

  useEffect(() => {
    const onFs = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  const toggleFs = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void box.current?.requestFullscreen?.();
  };

  const length = playback?.length ?? 0;
  const preparing = playback?.media_status === "preparing";
  const mediaError = playback?.media_status === "error" ? playback.media_error : null;

  return (
    <div ref={box} className="relative w-full overflow-hidden rounded-xl bg-black aspect-video border border-line/60 group">
      <video
        ref={video}
        className="size-full object-contain"
        playsInline
        preload="auto"
        disablePictureInPicture
        controlsList="nodownload noplaybackrate noremoteplayback"
        onWaiting={() => setBuffering(true)}
        onPlaying={() => setBuffering(false)}
        onCanPlay={() => setBuffering(false)}
        onContextMenu={(e) => e.preventDefault()}
        aria-label="Round video"
      />

      {!playback && (
        <Overlay>
          <p className="text-dim text-sm">The scene appears here once a start time is chosen.</p>
        </Overlay>
      )}
      {preparing && (
        <Overlay>
          <Reel />
          <p className="text-dim text-sm mt-3">Preparing the scene</p>
        </Overlay>
      )}
      {(mediaError || error) && (
        <Overlay>
          <AlertTriangle className="size-6 text-miss mb-2" aria-hidden />
          <p className="text-sm max-w-md text-center px-4">{mediaError || error}</p>
        </Overlay>
      )}
      {url && buffering && playback?.state === "playing" && (
        <div className="absolute top-3 right-3"><Reel small /></div>
      )}
      {needsTap && (
        <button
          className="absolute inset-0 flex items-center justify-center bg-black/40"
          onClick={() => {
            setMuted(false);
            setNeedsTap(false);
            void video.current?.play();
          }}
        >
          <span className="btn btn-lamp"><Volume2 className="size-4" /> Tap for sound</span>
        </button>
      )}

      {url && (
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent px-4 pt-8 pb-3 flex items-center gap-3 opacity-90 group-hover:opacity-100">
          <div className="flex-1">
            <div className="h-1 rounded bg-white/15 overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={length} aria-valuenow={Math.round(pos)}>
              <div className="h-full bg-lamp" style={{ width: `${length ? Math.min(100, (pos / length) * 100) : 0}%` }} />
            </div>
          </div>
          <span className="num text-xs text-dim w-24 text-right">
            {fmtTime(pos).replace(/^0:/, "")} / {fmtTime(length).replace(/^0:/, "")}
          </span>
          <button className="text-dim hover:text-screen" onClick={() => setMuted((m) => !m)} aria-label={muted ? "Unmute" : "Mute"}>
            {muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            onChange={(e) => {
              setVolume(Number(e.target.value));
              setMuted(Number(e.target.value) === 0);
            }}
            className="hidden sm:block w-20 accent-[var(--color-lamp)]"
            aria-label="Volume"
          />
          <button className="text-dim hover:text-screen" onClick={toggleFs} aria-label={fullscreen ? "Exit full screen" : "Full screen"}>
            {fullscreen ? <Minimize className="size-4" /> : <Maximize className="size-4" />}
          </button>
        </div>
      )}
      {showControlsNote && url && playback?.state !== "playing" && (
        <div className="absolute top-3 left-3 text-xs text-dim bg-black/60 rounded px-2 py-1">{showControlsNote}</div>
      )}
    </div>
  );
}

function Overlay({ children }: { children: React.ReactNode }) {
  return <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/70">{children}</div>;
}

export function Reel({ small = false }: { small?: boolean }) {
  const s = small ? 22 : 40;
  return (
    <svg width={s} height={s} viewBox="0 0 40 40" className="reel-spin text-lamp" aria-hidden>
      <circle cx="20" cy="20" r="18" fill="none" stroke="currentColor" strokeWidth="2.5" />
      {[0, 72, 144, 216, 288].map((a) => (
        <circle key={a} cx={20 + 9 * Math.cos((a * Math.PI) / 180)} cy={20 + 9 * Math.sin((a * Math.PI) / 180)} r="3.6" fill="currentColor" />
      ))}
      <circle cx="20" cy="20" r="2" fill="currentColor" />
    </svg>
  );
}
