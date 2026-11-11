import Link from "next/link";
import { Empty, ErrorState } from "@/components/ui";
import { api, ApiRequestError } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Citation {
  chunk_id: string; policy_key: string; policy_title: string; version_label: string; section: string; heading: string;
  page: number | null; effective_from: string; effective_to: string | null; historical: boolean; excerpt: string; score: number;
}
interface Answer {
  retrieval_id: string; outcome: string; abstained: boolean; as_of: string; retriever: string; citations: Citation[];
  evidence_gaps: { code: string; detail: string; owner_role: string }[]; message: string | null;
}

export default async function PolicySearch({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireSession();
  const sp = await searchParams;
  let answer: Answer | null = null;
  let error: ApiRequestError | null = null;
  if (sp.q) {
    try {
      answer = await api<Answer>("/api/v1/knowledge/search", {
        json: { query: sp.q, ...(sp.as_of ? { as_of: sp.as_of } : {}), ...(sp.case_id ? { case_id: sp.case_id } : {}) },
      });
    } catch (e) {
      if (!(e instanceof ApiRequestError)) throw e;
      error = e;
    }
  }
  return (
    <section aria-labelledby="h">
      <h1 id="h">Policy search</h1>
      <p className="muted">Only approved policy versions you are permitted to see, effective on the chosen date, are searched.
        No answer is invented: without an applicable source the search abstains. <Link href="/policies/admin">Policy administration</Link></p>
      <form method="get" className="stack panel">
        <label htmlFor="q">Question</label>
        <input id="q" name="q" defaultValue={sp.q ?? ""} required minLength={3} maxLength={500} />
        <label htmlFor="as_of">Applicable on (date, optional)</label>
        <input id="as_of" name="as_of" type="date" defaultValue={sp.as_of ?? ""} />
        <label htmlFor="case_id">Case (optional: applies the case's alert family and activity date)</label>
        <input id="case_id" name="case_id" defaultValue={sp.case_id ?? ""} />
        <button type="submit">Search</button>
      </form>
      {error ? <ErrorState error={error} what="Search" /> : null}
      {answer ? (
        <div className="panel" aria-live="polite">
          <p>Outcome <strong>{answer.outcome}</strong> (as of {answer.as_of}, {answer.retriever})</p>
          {answer.message ? <p className="warn">{answer.message}</p> : null}
          {answer.evidence_gaps.map((g) => <p key={g.code} className="warn">{g.code}: {g.detail} (owner {g.owner_role})</p>)}
          {answer.citations.length === 0 ? <Empty>No citations.</Empty> : (
            <ol>
              {answer.citations.map((c) => (
                <li key={c.chunk_id}>
                  <Link href={`/policies/chunks/${c.chunk_id}`}>{c.policy_title} v{c.version_label}, section {c.section} ({c.heading})</Link>
                  {c.historical ? <strong> - historical guidance</strong> : null}
                  <blockquote>{c.excerpt}</blockquote>
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
    </section>
  );
}
