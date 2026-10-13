import { cn } from "@/lib/utils";

/** PlugDrop wordmark, copied from the PlugDrop design system (Navigation.jsx). */
export function Wordmark({ className, suffix }: { className?: string; suffix?: string }) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <span className="flex size-8 items-center justify-center rounded-xl bg-pdblack">
        <span className="font-display text-lg font-semibold leading-none text-white">p</span>
      </span>
      <span className="font-display text-lg font-semibold tracking-tight text-pdblack">
        Plug<span className="text-pdpurple">Drop</span>
      </span>
      {suffix && <span className="eyebrow !tracking-[0.15em]">{suffix}</span>}
    </span>
  );
}
