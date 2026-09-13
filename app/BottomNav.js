"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: "/", label: "Home" },
  { href: "/bets", label: "Bets" },
  { href: "/legs", label: "Legs" },
  { href: "/futures", label: "Futures" },
  { href: "/history", label: "History" }
];

export default function BottomNav() {
  const pathname = usePathname();

  return (
    <nav className="bottomNav" aria-label="Primary navigation">
      {items.map((item) => {
        const active = item.href === "/"
          ? pathname === "/"
          : pathname === item.href || pathname.startsWith(`${item.href}/`);

        return (
          <Link
            key={item.href}
            href={item.href}
            className={active ? "isActive" : undefined}
            aria-current={active ? "page" : undefined}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
