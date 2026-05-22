import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Generator } from "./pages/Generator";
import { History } from "./pages/History";
import { BuildDetail } from "./pages/BuildDetail";

function Nav() {
  return (
    <nav className="nav">
      <a href="/" className="nav-brand">POE2 Build Architect</a>
      <div className="nav-links">
        <a href="/">Generator</a>
        <a href="/history">History</a>
      </div>
    </nav>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Nav />
      <main className="container">
        <Routes>
          <Route path="/" element={<Generator />} />
          <Route path="/history" element={<History />} />
          <Route path="/builds/:id" element={<BuildDetail />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
