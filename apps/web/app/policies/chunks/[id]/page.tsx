import { ErrorState, Field } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Chunk {
  chunk_id: string; chunk_key: string; policy_key: string; policy_title: string; version_label: string; approval_status: string;
  effective_from: string; effective_to: string | null; section: string; heading: string; page: number | null;
  text_hash: string; active: boolean; text: string | null; notice: string | null;
}

export default async function CitedPassage({ params }: { params: Promise<{ id: string }> }) {
  await requireSession();
  const { id } = await params;
  const { data: c, error } = await load<Chunk>(`/api/v1/knowledge/chunks/${id}`);
  if (error) return <ErrorState error={error} what="Cited passage" />;
  return (
    <section aria-labelledby="h">
      <h1 id="h">{c.policy_title} - version {c.version_label}, section {c.section}</h1>
      <div className="panel">
        <dl className="facts">
          <Field label="Status">{c.approval_status}{c.active ? "" : " (no longer in the active index)"}</Field>
          <Field label="Effective">{c.effective_from} to {c.effective_to ?? "open"}</Field>
          <Field label="Heading">{c.heading}</Field>
          <Field label="Location">section {c.section}{c.page ? `, page ${c.page}` : ""} ({c.chunk_key})</Field>
          <Field label="Content hash">{c.text_hash.slice(0, 16)} (identity, not truth)</Field>
        </dl>
      </div>
      {c.notice ? <p role="alert" className="flash flash-error">{c.notice}</p> : <blockquote className="panel">{c.text}</blockquote>}
    </section>
  );
}
