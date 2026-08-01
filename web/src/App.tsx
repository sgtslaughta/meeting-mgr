import { useEffect } from "react";
import { BrowserRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "./api";
import { AuthProvider } from "./AuthContext";
import { MeetingDetail } from "./routes/MeetingDetail";
import { MeetingList } from "./routes/MeetingList";
import { Login } from "./routes/Login";
import "./styles.css";

// No component below this one catches a 401 (session auth means any call
// can start failing mid-session, not just at load). Every unguarded api
// call rejects into an unhandled promise rejection, so catch it once here
// and send the user to login — skip when already there so Login's own
// try/catch (a caught rejection, never seen by this listener) isn't
// fought over and no redirect loop forms.
export function useAuthRedirect() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    function onRejection(e: PromiseRejectionEvent) {
      if (e.reason instanceof ApiError && e.reason.status === 401 &&
          location.pathname !== "/login") {
        navigate("/login");
      }
    }
    window.addEventListener("unhandledrejection", onRejection);
    return () => window.removeEventListener("unhandledrejection", onRejection);
  }, [location.pathname, navigate]);
}

function AppRoutes() {
  useAuthRedirect();
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<MeetingList />} />
      <Route path="/meetings/:id" element={<MeetingDetail />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
