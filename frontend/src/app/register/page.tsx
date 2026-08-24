"use client";

import Link from "next/link";
import { useActionState } from "react";
import { registerAction, type FormState } from "./actions";

const initialState: FormState = { error: null };

export default function RegisterPage() {
  const [state, formAction, pending] = useActionState(registerAction, initialState);

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="card w-full max-w-sm p-8">
        <h1 className="text-xl font-semibold mb-1">Create an account</h1>
        <p className="text-sm text-muted mb-6">Each account connects its own FPL team.</p>

        <form action={formAction} className="space-y-4">
          <div>
            <label className="label" htmlFor="email">Email</label>
            <input className="input" id="email" name="email" type="email" required autoFocus />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <input className="input" id="password" name="password" type="password" minLength={8} required />
          </div>
          {state.error && <p className="text-sm text-danger">{state.error}</p>}
          <button className="btn btn-primary w-full" type="submit" disabled={pending}>
            {pending ? "Creating…" : "Create account"}
          </button>
        </form>

        <p className="text-sm text-muted mt-6">
          Already have an account?{" "}
          <Link className="text-accent underline" href="/login">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
