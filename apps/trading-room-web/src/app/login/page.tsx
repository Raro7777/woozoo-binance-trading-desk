import type { Metadata } from "next";
import { LoginForm } from "../../components/login-form";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "Operator sign in" };

export default function LoginPage() {
  return (
    <>
      <PageHeading eyebrow="Local operator boundary" title="Sign in to the Paper desk">
        The password is sent only to the same-origin HTTPS session endpoint and is never retained by this interface.
      </PageHeading>
      <div className="grid">
        <section className="panel wide" aria-labelledby="operator-login">
          <h2 id="operator-login">Operator session</h2>
          <LoginForm />
        </section>
        <section className="panel" aria-labelledby="session-policy">
          <h2 id="session-policy">Session policy</h2>
          <p>Secure, HttpOnly, SameSite=Strict cookie. Every mutation uses a fresh one-time CSRF token and exact Origin validation.</p>
        </section>
      </div>
    </>
  );
}
