import React from "react";
import { Sparkle } from "./icons.jsx";

// The only screen shown before authentication (ADR 0008 decision 2):
// no chat, no sidebar, no API calls. `status` is "checking" while the
// app resolves a possible OIDC callback / stored session,
// "unconfigured" when the build has no issuer/client id, and "login"
// otherwise. `error` is a sign-in failure message to show above the
// button (e.g. a rejected callback), with the button left enabled so
// the user can retry.
export default function LoginScreen({ status, error, busy, onSignIn }) {
  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <Sparkle size={22} />
          <span className="brand-name">Agent</span>
        </div>
        {status === "checking" ? (
          <p className="login-text">Checking your sign-in…</p>
        ) : status === "unconfigured" ? (
          <>
            <h1 className="login-title">Sign-in isn’t configured</h1>
            <p className="login-text">
              Authentication is not configured for this build. Set{" "}
              <code>VITE_OIDC_ISSUER</code> and{" "}
              <code>VITE_OIDC_CLIENT_ID</code> at build time and rebuild —
              the app will not run without sign-in.
            </p>
          </>
        ) : (
          <>
            <h1 className="login-title">Sign in to continue</h1>
            <p className="login-text">
              Sign in with your identity provider to use Agent.
            </p>
            {error && (
              <div className="login-error" role="alert">
                {error}
              </div>
            )}
            <button
              className="login-btn"
              type="button"
              onClick={onSignIn}
              disabled={busy}
            >
              {busy ? "Redirecting…" : "Sign in"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
