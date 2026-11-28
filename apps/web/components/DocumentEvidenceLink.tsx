import Link from "next/link";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Navigation only; the document API rechecks case and tenant access. */
export function DocumentEvidenceLink({ sourceType, content }: { sourceType: string; content: unknown }) {
  if (sourceType !== "DOCUMENT_EXTRACTION" || !content || typeof content !== "object" || !("document_id" in content)) return null;
  const documentId = content.document_id;
  if (typeof documentId !== "string" || !UUID.test(documentId)) return null;
  return <p><Link href={`/documents/${documentId}`}>Open document review</Link><br /><small>The current document version may differ from this recorded evidence.</small></p>;
}
