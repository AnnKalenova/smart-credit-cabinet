import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Loader } from "./components/ui";
import { useAuth } from "./lib/auth";
import { LoginPage } from "./pages/LoginPage";
import { BankApplicationPage } from "./pages/bank/BankApplicationPage";
import { BankDashboard } from "./pages/bank/BankDashboard";
import { BankQueue } from "./pages/bank/BankQueue";
import { BankSettings } from "./pages/bank/BankSettings";
import { ApplicationPage } from "./pages/client/ApplicationPage";
import { ClientHome } from "./pages/client/ClientHome";
import { LoanPage } from "./pages/client/LoanPage";
import { MatchPage } from "./pages/client/MatchPage";

function Guard({ area, children }: { area: "client" | "bank"; children: ReactNode }) {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <Loader />;
  if (!me) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  const isClient = me.user.role === "CLIENT";
  if (area === "client" && !isClient) return <Navigate to="/b" replace />;
  if (area === "bank" && isClient) return <Navigate to="/c" replace />;
  return <Layout area={area}>{children}</Layout>;
}

function Home() {
  const { me, loading } = useAuth();
  if (loading) return <Loader />;
  if (!me) return <Navigate to="/login" replace />;
  return <Navigate to={me.user.role === "CLIENT" ? "/c" : "/b"} replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<Home />} />
      <Route path="/c" element={<Guard area="client"><ClientHome /></Guard>} />
      <Route path="/c/match" element={<Guard area="client"><MatchPage /></Guard>} />
      <Route path="/c/applications/:id" element={<Guard area="client"><ApplicationPage /></Guard>} />
      <Route path="/c/loans/:id" element={<Guard area="client"><LoanPage /></Guard>} />
      <Route path="/b" element={<Guard area="bank"><BankQueue /></Guard>} />
      <Route path="/b/applications/:id" element={<Guard area="bank"><BankApplicationPage /></Guard>} />
      <Route path="/b/dashboard" element={<Guard area="bank"><BankDashboard /></Guard>} />
      <Route path="/b/settings" element={<Guard area="bank"><BankSettings /></Guard>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
