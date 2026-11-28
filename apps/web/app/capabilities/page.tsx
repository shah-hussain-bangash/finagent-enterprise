import Link from "next/link";
import { requireSession } from "@/lib/session";
import { sessionIdentity } from "@/lib/session-identity";
import { CAPABILITIES, CAPABILITY_DETAILS } from "@/lib/capability-map";

export const metadata = { title: "Capability map | FinAgent Enterprise" };

export default async function CapabilitiesPage() {
  const session = await requireSession();
  const identity = await sessionIdentity(session);
  const permissions = new Set(identity?.permissions ?? []);
  return <section className="portal-section capabilities-page" aria-labelledby="h">
    <div className="portal-page-heading"><p className="portal-eyebrow">FINAGENT / PRODUCT CAPABILITIES</p><h1 id="h">End-to-end capability map</h1>
      <p>Twenty areas for a governed financial investigation platform, presented in development sequence.</p></div>
    <div className="capabilities-notice" role="note"><strong>Development in progress</strong><p>These areas include existing functions, previews and work still to be built. A linked view does not mean the whole area is complete or approved for production.</p></div>
    <ol className="capabilities-map">{CAPABILITIES.map((item, index) => {
      const available = item.link && item.link.permissions.some(permission => permissions.has(permission));
      return <li key={item.title}><article className="capabilities-map-card">
        <span className="capabilities-number">{String(index + 1).padStart(2, "0")}</span>
        <div><h2 id={`capability-${index + 1}`}>{item.title}</h2><p>{item.description}</p><p className="capabilities-remaining"><strong>Remaining:</strong> {item.remaining}</p>
          <details><summary>View capability scope</summary><ul>{CAPABILITY_DETAILS[index]?.map(detail => <li key={detail}>{detail}</li>)}</ul></details>
          {available && item.link && <Link href={item.link.href}><span>{item.link.kind} · </span>{item.link.label} →</Link>}
        </div>
      </article></li>;
    })}</ol>
  </section>;
}
