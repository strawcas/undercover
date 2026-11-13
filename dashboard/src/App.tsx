import { Link, Navigate, NavLink, Route, Routes } from "react-router";
import { ClaimPage } from "./pages/ClaimPage";
import { ConfigurationPage } from "./pages/ConfigurationPage";
import { SettingsPage } from "./pages/SettingsPage";
import { QueuePage } from "./pages/QueuePage";
import { ScoreboardPage } from "./pages/ScoreboardPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import {
  CLAIM_PAGE,
  CONFIGURATION_PAGE,
  EVALUATION_PAGE,
  SCOREBOARD_PAGE,
  SETTINGS_PAGE,
} from "./paths";
import { useAuth } from "./auth";
import { SignInPage } from "./pages/SignInPage";

export function App() {
  const auth = useAuth();
  if (auth.loading)
    return (
      <main className="page">
        <p className="message">Restoring your sign-in…</p>
      </main>
    );
  if (!auth.handler) return <SignInPage />;
  return (
    <>
      <header className="header">
        <Link to="/" className="brand">
          UnderCover <span className="tagline">claims pilot</span>
        </Link>
        <nav className="nav" aria-label="Main">
          <NavLink to="/" end>
            Queue
          </NavLink>
          <NavLink to={CONFIGURATION_PAGE}>Configuration</NavLink>
          <NavLink to={SETTINGS_PAGE}>Settings</NavLink>
          <NavLink to={SCOREBOARD_PAGE}>Scoreboard</NavLink>
        </nav>
        <div className="session">
          <span>{auth.handler.name}</span>
          <button
            type="button"
            onClick={() => {
              void auth.signOut();
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="page">
        {auth.error && (
          <p className="form-error" role="alert">
            {auth.error}
          </p>
        )}
        <Routes>
          <Route path="/" element={<QueuePage />} />
          <Route path={CLAIM_PAGE} element={<ClaimPage />} />
          <Route path={CONFIGURATION_PAGE} element={<ConfigurationPage />} />
          <Route path={SETTINGS_PAGE} element={<SettingsPage />} />
          <Route
            path="/instructions"
            element={<Navigate to={CONFIGURATION_PAGE} replace />}
          />
          <Route path={SCOREBOARD_PAGE} element={<ScoreboardPage />} />
          <Route path={EVALUATION_PAGE} element={<EvaluationPage />} />
          <Route path="*" element={<p className="message">Page not found</p>} />
        </Routes>
      </main>
    </>
  );
}
