"use client";

import Link from "next/link";
import { useActionState } from "react";
import { loginAction, type FormState } from "./actions";

const initialState: FormState = { error: null };

export default function LoginPage() {
  const [state, formAction, pending] = useActionState(loginAction, initialState);

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="card w-full max-w-sm p-8">
        <h1 className="text-xl font-semibold mb-1">fpl-optimizer</h1>
        <p className="text-sm text-muted mb-6">Sign in to your account.</p>

        <form action={formAction} className="space-y-4">
          <div>
            <label className="label" htmlFor="email">Email</label>
            <input className="input" id="email" name="email" type="email" required autoFocus />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <input className="input" id="password" name="password" type="password" required />
          </div>
          {state.error && <p className="text-sm text-danger">{state.error}</p>}
          <button className="btn btn-primary w-full" type="submit" disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="text-sm text-muted mt-6">
          No account yet?{" "}
          <Link className="text-accent underline" href="/register">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}
