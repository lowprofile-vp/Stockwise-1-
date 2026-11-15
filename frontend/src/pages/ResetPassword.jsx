import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api, { errorMessage } from "../services/api";
import { authError, supabase, supabaseEnabled } from "../services/supabase";
import { btnPrimary, inputClass, labelClass } from "../utils/styles";
import { AuthShell } from "./Login";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(!supabaseEnabled);

  useEffect(() => {
    if (!supabaseEnabled) return undefined;
    // The Supabase client reads the recovery link as soon as it loads, which can be
    // before this page subscribes, so the PASSWORD_RECOVERY event may already be gone.
    // Check for the session it created as well as listening for new events.
    let active = true;
    supabase.auth.getSession().then(({ data }) => {
      if (active && data.session) setReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === "PASSWORD_RECOVERY" || event === "SIGNED_IN" || (event === "INITIAL_SESSION" && session)) setReady(true);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, []);

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (password !== confirm) {
      setError("Password and confirmation do not match.");
      return;
    }
    setLoading(true);
    try {
      if (supabaseEnabled) {
        if (!ready) {
          setError("Open the reset link from the Supabase email, then choose a new password.");
          return;
        }
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw authError(error);
        setMessage("Password updated. You can log in with the new password.");
      } else {
        const response = await api.post("/auth/reset-password", { token: params.get("token"), password, confirm_password: confirm });
        setMessage(response.data.message);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell title="Choose a new password" subtitle={supabaseEnabled ? "Use the link from the Supabase email. It signs you in only long enough to set a new password." : "This link expires 30 minutes after it is created."}>
      <form onSubmit={submit} className="space-y-4">
        <label className="block"><span className={labelClass}>New password</span><input className={inputClass} type="password" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <label className="block"><span className={labelClass}>Confirm password</span><input className={inputClass} type="password" required value={confirm} onChange={(event) => setConfirm(event.target.value)} /></label>
        {error ? <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p> : null}
        {message ? <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message} <Link className="font-medium underline" to="/login">Log in</Link></p> : null}
        <button className={`${btnPrimary} w-full`} disabled={loading || Boolean(message)}>{loading ? "Updating..." : "Update password"}</button>
      </form>
    </AuthShell>
  );
}
