"use client";

export default function RouteError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <section className="panel route-state" role="alert">
    <h1>We couldn’t load this page</h1>
    <p>Try again. If the problem continues, contact your support team.</p>
    <button type="button" onClick={reset}>Try again</button>
  </section>;
}
