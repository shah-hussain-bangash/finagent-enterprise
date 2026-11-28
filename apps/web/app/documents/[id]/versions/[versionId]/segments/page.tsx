import Link from "next/link";
import { Empty, ErrorState, Json } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface SegmentPage { version_id: string; available: boolean; items: unknown[]; next_offset: number | null }

export default async function DocumentSegments({ params, searchParams }: {
  params: Promise<{ id: string; versionId: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { id, versionId } = await params;
  const query = await searchParams;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!uuid.test(id) || !uuid.test(versionId)) return <section><h1>Segments unavailable</h1><p>The document or version identifier is invalid.</p></section>;
  const validOffset = query.offset === undefined || /^(0|[1-9][0-9]{0,6})$/.test(query.offset);
  const offset = validOffset && query.offset ? Number(query.offset) : 0;
  const path = `/documents/${id}/versions/${versionId}/segments${offset ? `?offset=${offset}` : ""}`;
  await requireSession(path);
  const result = await load<SegmentPage>(`/api/v1/documents/${id}/versions/${versionId}/segments?offset=${offset}&limit=50`, path);
  const page = result.data;
  const invalidResponse = page && (page.version_id?.toLowerCase() !== versionId.toLowerCase()
    || typeof page.available !== "boolean" || !Array.isArray(page.items) || page.items.length > 50
    || (page.next_offset !== null && (!Number.isSafeInteger(page.next_offset) || page.next_offset <= offset)));
  return <section aria-labelledby="h">
    <h1 id="h">Extracted document segments</h1>
    <p><Link href={`/documents/${id}`}>← Back to document review</Link></p>
    <p>Recorded extraction for version {versionId.slice(0, 8)}. Extracted text is not proof of authenticity.</p>
    {!validOffset && <p role="alert">The requested page was invalid. Showing the first page.</p>}
    {result.error && <ErrorState error={result.error} what="Extracted segments" />}
    {invalidResponse && <p role="alert">The segment response could not be verified for this document version. Check the connected API before continuing.</p>}
    {page && !invalidResponse && (page.available ? <>
      {page.items.length === 0 ? <Empty>No segments were returned at this position.</Empty> : <>
        <p>Showing segments {offset + 1}–{offset + page.items.length}.</p>
        <Json value={page.items} />
      </>}
      <nav aria-label="Extracted segment pages">
        {offset > 0 && <Link href={`/documents/${id}/versions/${versionId}/segments?offset=${Math.max(0, offset - 50)}`}>← Previous segments</Link>}
        {page.next_offset !== null && <> <Link href={`/documents/${id}/versions/${versionId}/segments?offset=${page.next_offset}`}>Next segments →</Link></>}
      </nav>
    </> : <p role="status">Extracted content is unavailable for this version.</p>)}
  </section>;
}
