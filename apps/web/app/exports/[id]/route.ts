// Authorized export download (FR-030). The API re-checks case access at download time.
import { raw } from "@/lib/api";

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await params;
  const res = await raw(`/api/v1/exports/${encodeURIComponent(id)}/content`);
  if (!res.ok) return new Response("Export not permitted or not found.", { status: res.status });
  return new Response(res.body, {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Content-Disposition": res.headers.get("content-disposition") ?? "attachment",
      "Cache-Control": "no-store",
    },
  });
}
