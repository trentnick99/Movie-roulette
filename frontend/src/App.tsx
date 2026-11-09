import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { BarChart3, Clapperboard, Film, History, Home, LayoutGrid, Settings, Users } from "lucide-react";
import { api } from "./lib/api";
import { Spinner, TmdbAttribution, ToastProvider } from "./components/ui";
import LoginPage from "./pages/Login";
import JoinPage from "./pages/Join";

// Pages load on demand so a phone joining a game downloads only what the player screen needs.
const HomePage = lazy(() => import("./pages/Home"));
const PlayPage = lazy(() => import("./pages/Play"));
const HostGamePage = lazy(() => import("./pages/HostGame"));
const LibraryPage = lazy(() => import("./pages/Library"));
const CategoriesPage = lazy(() => import("./pages/Categories"));
const PlayersPage = lazy(() => import("./pages/Players"));
const HistoryPage = lazy(() => import("./pages/History"));
const GameReview = lazy(() => import("./pages/History").then((m) => ({ default: m.GameReview })));
const StatsPage = lazy(() => import("./pages/Stats"));
const SettingsPage = lazy(() => import("./pages/Settings"));
const PlayerGamePage = lazy(() => import("./pages/PlayerGame"));

const NAV = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/play", label: "Play Game", icon: Clapperboard },
  { to: "/library", label: "Movie Library", icon: Film },
  { to: "/categories", label: "Categories", icon: LayoutGrid },
  { to: "/players", label: "Players", icon: Users },
  { to: "/history", label: "Game History", icon: History },
  { to: "/stats", label: "Statistics", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
];

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen md:flex">
      <aside className="hidden md:flex md:w-60 shrink-0 flex-col border-r border-line/70 bg-house/60 px-4 py-6 sticky top-0 h-screen">
        <div className="px-2 mb-8">
          <div className="title text-2xl leading-none">Movie Roulette</div>
          <div className="text-xs text-faint mt-1.5">Your collection, one scene at a time</div>
        </div>
        <nav className="flex flex-col gap-0.5" aria-label="Main">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                  isActive ? "bg-panel-2 text-screen" : "text-dim hover:text-screen hover:bg-panel"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={`size-4 ${isActive ? "text-lamp" : ""}`} aria-hidden />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-2">
          <TmdbAttribution compact />
        </div>
      </aside>
      <main className="flex-1 min-w-0 px-4 md:px-10 py-6 md:py-10 pb-28 md:pb-10">
        <Suspense fallback={<Spinner />}>{children}</Suspense>
      </main>
      <nav
        aria-label="Main"
        className="md:hidden fixed bottom-0 inset-x-0 z-30 flex justify-around border-t border-line bg-house/95 backdrop-blur px-1 py-1.5"
      >
        {NAV.filter((n) => ["/", "/play", "/library", "/players", "/settings"].includes(n.to)).map(
          ({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex flex-col items-center gap-0.5 px-2 py-1 text-[11px] ${isActive ? "text-lamp" : "text-dim"}`
              }
            >
              <Icon className="size-5" aria-hidden />
              {label.split(" ")[0]}
            </NavLink>
          ),
        )}
      </nav>
    </div>
  );
}

/** Host area gate: signs in automatically with the launch link or on the host PC itself. */
function HostGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "ok" | "login">("checking");
  const loc = useLocation();
  const nav = useNavigate();
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.get<{ host: boolean; can_login_without_password: boolean }>("/api/auth/me");
        if (me.host) return !cancelled && setState("ok");
        const launch = new URLSearchParams(loc.search).get("launch");
        if (launch || me.can_login_without_password) {
          try {
            await api.post("/api/auth/host/login", launch ? { launch_token: launch } : {});
            if (launch) nav(loc.pathname, { replace: true });
            return !cancelled && setState("ok");
          } catch {
            /* fall through to the login form */
          }
        }
        if (!cancelled) setState("login");
      } catch {
        if (!cancelled) setState("login");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const onUnauthorized = () => setState("login");
    window.addEventListener("mr:host-unauthorized", onUnauthorized);
    return () => window.removeEventListener("mr:host-unauthorized", onUnauthorized);
  }, []);
  if (state === "checking") return <div className="p-10"><Spinner label="Opening Movie Roulette" /></div>;
  if (state === "login") return <LoginPage onDone={() => setState("ok")} />;
  return <Shell>{children}</Shell>;
}

function Appearance() {
  useEffect(() => {
    api
      .get<{ sections: { appearance: { theme: string; animations: boolean } } }>("/api/settings")
      .then((s) => {
        document.documentElement.dataset.theme = s.sections.appearance.theme;
        document.documentElement.dataset.motion = s.sections.appearance.animations ? "on" : "off";
      })
      .catch(() => {});
  }, []);
  return null;
}

export default function App() {
  return (
    <ToastProvider>
      <BrowserRouter>
        <Suspense fallback={<div className="p-10"><Spinner /></div>}>
        <Routes>
          <Route path="/join" element={<JoinPage />} />
          <Route path="/player" element={<PlayerGamePage />} />
          <Route
            path="/*"
            element={
              <HostGate>
                <Appearance />
                <Routes>
                  <Route path="/" element={<HomePage />} />
                  <Route path="/play" element={<PlayPage />} />
                  <Route path="/game/:id" element={<HostGamePage />} />
                  <Route path="/library" element={<LibraryPage />} />
                  <Route path="/categories" element={<CategoriesPage />} />
                  <Route path="/players" element={<PlayersPage />} />
                  <Route path="/history" element={<HistoryPage />} />
                  <Route path="/history/:id" element={<GameReview />} />
                  <Route path="/stats" element={<StatsPage />} />
                  <Route path="/settings" element={<SettingsPage />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </HostGate>
            }
          />
        </Routes>
        </Suspense>
      </BrowserRouter>
    </ToastProvider>
  );
}
