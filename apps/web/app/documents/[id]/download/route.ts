// Streams a document through the server: request a short-lived, principal-bound link, then redeem it
// with the same session. The API re-checks current permission at download time (SPR-011/012).
import { api, ApiRequestError, raw } from "@/lib/api";

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await params;
  try {
    const link = await api<{ url: string }>(`/api/v1/documents/${id}/download-link`);
    const res = await raw(link.url);
    if (!res.ok) return new Response("Download not permitted or no longer available.", { status: res.status });
    return new Response(res.body, {
      headers: {
        "Content-Type": res.headers.get("content-type") ?? "application/octet-stream",
        "Content-Disposition": res.headers.get("content-disposition") ?? "attachment",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (e) {
    if (e instanceof ApiRequestError) return new Response(e.message, { status: e.status });
    throw e;
  }
}
