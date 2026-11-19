import Link from "next/link";

export default function NotFound() {
  return <section className="panel route-state">
    <h1>Page not found</h1>
    <p>This page may have moved or may not be available.</p>
    <Link href="/">Return to overview</Link>
  </section>;
}
