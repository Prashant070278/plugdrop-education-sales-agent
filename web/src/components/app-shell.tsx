"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import {
  LayoutDashboard, Building2, Users, KanbanSquare, Briefcase, CheckSquare, Settings, LogOut, Menu, X,
} from "lucide-react";
import { Wordmark } from "@/components/brand";
import { AuthProvider, useUser } from "@/components/auth";
import { api } from "@/lib/api";
import { label } from "@/lib/format";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/institutions", label: "Institutions", icon: Building2 },
  { href: "/contacts", label: "Contacts", icon: Users },
  { href: "/leads", label: "Lead pipeline", icon: KanbanSquare },
  { href: "/opportunities", label: "Opportunities", icon: Briefcase },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/settings", label: "Settings", icon: Settings },
];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  return (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {NAV.map(({ href, label: text, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} onClick={onNavigate} aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              active ? "bg-pdpurple-bg text-pdpurple-deep" : "text-pdblack/70 hover:bg-muted hover:text-pdblack",
            )}>
            <Icon className={cn("size-4", active ? "text-pdpurple-deep" : "text-muted-foreground")} />
            {text}
          </Link>
        );
      })}
    </nav>
  );
}

function UserBox() {
  const user = useUser();
  const router = useRouter();
  const logout = async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  };
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-background p-2.5">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-pdpurple-bg text-xs font-semibold text-pdpurple-deep">
        {user?.full_name.split(" ").map((p) => p[0]).slice(0, 2).join("") ?? "…"}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{user?.full_name ?? "Loading…"}</div>
        <div className="truncate text-xs text-muted-foreground">{user ? label(user.role) : ""}</div>
      </div>
      <button onClick={logout} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Sign out" title="Sign out">
        <LogOut className="size-4" />
      </button>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <AuthProvider>
      <div className="flex min-h-screen">
        <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col gap-6 border-r bg-[#FCFBFF] px-3 py-5 lg:flex">
          <Link href="/" className="px-2"><Wordmark /></Link>
          <div className="eyebrow px-3 -mb-3">Sales CRM</div>
          <NavLinks />
          <div className="mt-auto"><UserBox /></div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-30 flex items-center justify-between border-b bg-background/90 px-4 py-3 backdrop-blur lg:hidden">
            <Link href="/"><Wordmark /></Link>
            <button onClick={() => setOpen(true)} aria-label="Open menu" className="rounded-md p-2 hover:bg-muted"><Menu className="size-5" /></button>
          </header>
          {open && (
            <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true">
              <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
              <div className="absolute inset-y-0 left-0 flex w-72 flex-col gap-6 bg-background p-4 shadow-xl">
                <div className="flex items-center justify-between"><Wordmark /><button aria-label="Close menu" onClick={() => setOpen(false)}><X className="size-5" /></button></div>
                <NavLinks onNavigate={() => setOpen(false)} />
                <div className="mt-auto"><UserBox /></div>
              </div>
            </div>
          )}
          <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">{children}</main>
        </div>
      </div>
    </AuthProvider>
  );
}
