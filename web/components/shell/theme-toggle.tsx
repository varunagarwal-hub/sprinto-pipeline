"use client";

import { useSyncExternalStore } from "react";
import { useTheme } from "next-themes";
import { Monitor, Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";

const OPTIONS = [
  { value: "light", icon: Sun, label: "Light" },
  { value: "dark", icon: Moon, label: "Dark" },
  { value: "system", icon: Monitor, label: "System" },
] as const;

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  // true only on the client, so the active theme is never guessed during server render
  const mounted = useSyncExternalStore(() => () => {}, () => true, () => false);
  return (
    <div className="flex rounded-lg border border-sidebar-border bg-background/60 p-0.5" role="radiogroup" aria-label="Theme">
      {OPTIONS.map(({ value, icon: Icon, label }) => (
        <button
          key={value}
          role="radio"
          aria-checked={mounted && theme === value}
          aria-label={label}
          onClick={() => setTheme(value)}
          className={cn(
            "flex flex-1 items-center justify-center rounded-md py-1.5 transition-colors",
            mounted && theme === value ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
