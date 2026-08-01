import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../auth";
import { useAuth } from "../AuthContext";

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { refresh } = useAuth();
  const navigate = useNavigate();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login(email, password);
      refresh();
      navigate("/");
    } catch {
      setError("invalid email or password");
    }
  }

  return (
    <form onSubmit={onSubmit}>
      <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="email" />
      <input value={password} onChange={(e) => setPassword(e.target.value)} type="password"
             placeholder="password" />
      <button type="submit">Sign in</button>
      <a href="/auth/oidc/login">Sign in with SSO</a>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
