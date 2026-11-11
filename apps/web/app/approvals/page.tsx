import Link from "next/link";
import { Empty, ErrorState, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Req { approval_request_id: string; case_id: string; status: string; created_at: string; reviewer_id: string | null; requested_by: string }

export default async function Approvals({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireSession();
  const sp = await searchParams;
  const status = sp.status ?? "PENDING";
  const { data, error } = await load<{ items: Req[] }>(`/api/v1/approvals?status=${encodeURIComponent(status)}`);
  return (
    <section aria-labelledby="h">
      <h1 id="h">Approval center</h1>
      <form className="inline" method="get">
        <label htmlFor="status">Status</label>
        <select id="status" name="status" defaultValue={status}>
          {["PENDING", "APPROVED", "RETURNED", "INFO_REQUESTED", "REJECTED", "INVALIDATED", "WITHDRAWN", "CONSUMED"].map((s) =>
            <option key={s}>{s}</option>)}
        </select>
        <button type="submit">Filter</button>
      </form>
      {error ? <ErrorState error={error} what="Approvals" /> : null}
      {data && data.items.length === 0 ? <Empty>No review requests with this status in your scope.</Empty> : null}
      {data && data.items.length > 0 ? (
        <table>
          <thead><tr><th>Request</th><th>Case</th><th>Status</th><th>Reviewer</th><th>Submitted</th></tr></thead>
          <tbody>
            {data.items.map((r) => (
              <tr key={r.approval_request_id}>
                <td><Link href={`/approvals/${r.approval_request_id}`}>{r.approval_request_id.slice(0, 8)}</Link></td>
                <td><Link href={`/cases/${r.case_id}`}>{r.case_id.slice(0, 8)}</Link></td>
                <td>{r.status}</td>
                <td>{r.reviewer_id?.slice(0, 8) ?? "-"}</td>
                <td><When iso={r.created_at} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
