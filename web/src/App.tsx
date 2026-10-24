import { useLayoutEffect } from "react";
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useLocation, useParams } from "react-router-dom";

import { AuthProvider, RequireAuth } from "./lib/auth";
import { applyArea } from "./lib/theme";
import { Call } from "./pages/Call";
import { BalancePage } from "./pages/BalancePage";
import { DocumentPage } from "./pages/DocumentPage";
import { Home } from "./pages/Home";
import { Help } from "./pages/info/Help";
import { Privacy } from "./pages/info/Privacy";
import { Refunds } from "./pages/info/Refunds";
import { Terms } from "./pages/info/Terms";
import { Landing } from "./pages/Landing";
import { NotFound } from "./pages/NotFound";
import { Settings } from "./pages/Settings";
import { SignIn } from "./pages/SignIn";

const router = createBrowserRouter([{ element: <Root />, children: [
  { path: "/", element: <Landing /> },
  { path: "/app", element: <RequireAuth><Home /></RequireAuth> },
  { path: "/app/balance", element: <RequireAuth><BalancePage /></RequireAuth> },
  { path: "/app/credits", element: <Navigate to="/app/balance" replace /> },   // its old name
  { path: "/app/settings", element: <RequireAuth><Settings /></RequireAuth> },
  { path: "/app/call", element: <RequireAuth><Call /></RequireAuth> },
  { path: "/app/documents/:id", element: <RequireAuth><DocumentPage visibility="private" /></RequireAuth> },
  { path: "/sign-in", element: <SignIn /> },
  { path: "/help", element: <Help /> },
  { path: "/privacy", element: <Privacy /> },
  { path: "/terms", element: <Terms /> },
  { path: "/refunds", element: <Refunds /> },
  { path: "/call", element: <OldCallLink /> },
  { path: "/documents/:id", element: <OldDocumentLink /> },
  { path: "/d/:token", element: <DocumentPage visibility="public" /> },
  { path: "*", element: <NotFound /> },
] }]);

/** Every page: the console's pages take the chosen theme, the rest stay light (also after a redirect to sign-in). */
function Root() {
  const { pathname } = useLocation();
  useLayoutEffect(() => applyArea(pathname), [pathname]);
  return <Outlet />;
}

/** "Talk in browser" is for signed-up accounts: /call is the console's call page (signed out: sign-in first). */
function OldCallLink() {
  const { search } = useLocation();
  return <Navigate to={`/app/call${search}`} replace />;
}

function OldDocumentLink() {
  const { id } = useParams();
  return <Navigate to={`/app/documents/${id ?? ""}`} replace />;
}

export function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}
