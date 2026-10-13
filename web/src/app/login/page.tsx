"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Wordmark } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/login", { method: "POST", json: { email, password } });
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid gap-4">
      <div className="grid gap-1.5">
        <Label htmlFor="email">Work email</Label>
        <Input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="password">Password</Label>
        <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button type="submit" size="lg" disabled={busy}>{busy && <Loader2 className="animate-spin" />}Sign in</Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col justify-center px-6 py-12 sm:px-12">
        <div className="mx-auto w-full max-w-sm">
          <Wordmark className="mb-10" />
          <div className="eyebrow mb-2">Internal sales CRM</div>
          <h1 className="mb-2 text-4xl font-medium text-pdblack">Sign in</h1>
          <p className="mb-8 text-sm text-muted-foreground">For the PlugDrop.AI education sales team.</p>
          <Suspense><LoginForm /></Suspense>
        </div>
      </div>
      <div className="relative hidden overflow-hidden bg-pdpurple-bg lg:block">
        <div className="absolute inset-0" style={{
          backgroundImage: "radial-gradient(at 12% 18%, rgba(167,139,250,0.35) 0px, transparent 50%), radial-gradient(at 88% 0%, rgba(139,92,246,0.20) 0px, transparent 50%), linear-gradient(rgba(139,92,246,0.07) 1px, transparent 1px), linear-gradient(90deg, rgba(139,92,246,0.07) 1px, transparent 1px)",
          backgroundSize: "auto, auto, 48px 48px, 48px 48px",
        }} />
        <div className="relative flex h-full flex-col justify-end p-12">
          <div className="mb-6 flex h-16 items-end gap-1.5" aria-hidden>
            {[30, 60, 100, 45, 80, 35, 70].map((h, i) => (
              <span key={i} className="w-3 rounded-full bg-pdpurple" style={{ height: `${h}%`, opacity: i % 2 ? 0.55 : 1 }} />
            ))}
          </div>
          <p className="max-w-md font-display text-3xl font-medium leading-tight tracking-tight text-pdblack">
            Voice AI + CRM for universities, colleges and EdTech.
          </p>
          <p className="mt-3 max-w-md text-sm text-pdblack/70">Track institutions, contacts, pipeline and outreach in one place.</p>
        </div>
      </div>
    </div>
  );
}
