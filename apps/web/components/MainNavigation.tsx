"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function MainNavigation({ items }: { items: { href: string; label: string }[] }) {
  const pathname = usePathname();
  return <nav aria-label="Main">
    {items.map(({ href, label }) => {
      const current = pathname === href || (href !== "/" && pathname?.startsWith(`${href}/`));
      return <Link key={href} href={href} aria-current={current ? "page" : undefined}
        className={href === "/workspace" ? "workspace-nav-button" : undefined}>{label}</Link>;
    })}
  </nav>;
}
