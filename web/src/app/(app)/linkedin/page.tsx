"use client";
import { useState } from "react";
import { Lightbulb } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/common";
import { LinkedInItemCard } from "@/components/engagement";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import type { LinkedinItem } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function LinkedInPage() {
  const toast = useToast();
  const [tab, setTab] = useState<"todo" | "ideas" | "done">("todo");
  const { data, mutate } = useApi<LinkedinItem[]>("/linkedin/items");
  const items = data ?? [];
  const shown = items.filter((i) =>
    tab === "ideas" ? i.kind === "content_suggestion" && i.status !== "done" : tab === "done" ? i.status === "done" : i.kind !== "content_suggestion" && i.status !== "done",
  );
  const ideas = async () => {
    try { const r = await api<LinkedinItem[]>("/linkedin/content-ideas", { method: "POST" }); toast("success", `${r.length} post ideas added`); setTab("ideas"); mutate(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <>
      <PageHeader eyebrow="LinkedIn assistant" title="LinkedIn"
        description="Research notes, message drafts and post ideas. There is no automation: you copy the text into LinkedIn yourself and record it here."
        actions={<Button variant="outline" onClick={ideas}><Lightbulb />Suggest post ideas</Button>} />
      <div className="mb-4 flex flex-wrap gap-1.5">
        {([["todo", "Notes and drafts"], ["ideas", "Post ideas"], ["done", "Done"]] as const).map(([k, t]) => (
          <button key={k} onClick={() => setTab(k)} aria-pressed={tab === k} className={cn("rounded-full border px-3 py-1 text-sm font-medium", tab === k ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>{t}</button>
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{tab === "ideas" ? "Post ideas from your research themes" : tab === "done" ? "Done by hand" : "Notes and drafts"}</CardTitle>
          <CardDescription className="mt-1">{tab === "todo" ? "Create drafts from a lead (People and channels → LinkedIn). Connection notes are limited to 300 characters." : tab === "ideas" ? "Ideas never name prospects or use figures that aren't approved." : "Recorded on each lead's timeline."}</CardDescription>
        </CardHeader>
        <CardContent>
          {!data ? <Skeleton className="h-32" /> : shown.length === 0 ? <p className="text-sm text-muted-foreground">Nothing here yet.</p> : (
            <ul className="grid gap-3">{shown.map((i) => <LinkedInItemCard key={i.id} item={i} onChange={() => mutate()} />)}</ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}
