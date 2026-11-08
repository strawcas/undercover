import { Link, NavLink, Route, Routes } from "react-router";
import { ClaimPage } from "./pages/ClaimPage";
import { InstructionsPage } from "./pages/InstructionsPage";
import { QueuePage } from "./pages/QueuePage";
import { ScoreboardPage } from "./pages/ScoreboardPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { CLAIM_PAGE, EVALUATION_PAGE, SCOREBOARD_PAGE } from "./paths";

export function App() {
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
          <NavLink to="/instructions">Instructions</NavLink>
          <NavLink to={SCOREBOARD_PAGE}>Scoreboard</NavLink>
        </nav>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<QueuePage />} />
          <Route path={CLAIM_PAGE} element={<ClaimPage />} />
          <Route path="/instructions" element={<InstructionsPage />} />
          <Route path={SCOREBOARD_PAGE} element={<ScoreboardPage />} />
          <Route path={EVALUATION_PAGE} element={<EvaluationPage />} />
          <Route path="*" element={<p className="message">Page not found</p>} />
        </Routes>
      </main>
    </>
  );
}
