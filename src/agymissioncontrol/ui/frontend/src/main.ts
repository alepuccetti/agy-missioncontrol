import Alpine from "alpinejs";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Cpu,
  DollarSign,
  Layers,
  Lock,
  PauseCircle,
  Play,
  Radio,
  RefreshCw,
  ShieldAlert,
  Square,
  TrendingDown,
  Unlock,
  createIcons,
} from "lucide";

export interface SessionRecord {
  pid: number;
  project_name: string;
  project_dir: string;
  label: string;
  model: string;
  quota_group: string;
  state: "ACTIVE" | "SLEEPING" | "COMPLETED" | "STALE";
  wakes_at?: string | null;
  remaining_sleep_seconds?: number | null;
  credit_burn_usd: number;
  updated_at: string;
}

export interface QuotaPoolStatus {
  model_family: string;
  rolling_pct?: number | null;
  rolling_reset_at?: string | null;
  weekly_pct?: number | null;
  weekly_reset_at?: string | null;
  locked: boolean;
}

export interface SharedLockRecord {
  model: string;
  locked_until: string;
  locked_by_pid: number | null;
  reason: string;
  updated_at: string;
  remaining_seconds?: number;
}

export interface QuotaHistoryPoint {
  timestamp: string;
  model_family: string;
  limit_type: string;
  remaining_pct: number;
  reset_at: string;
}

export interface ChartSeries {
  key: string;
  modelFamily: string;
  limitType: string;
  color: string;
  dashed: boolean;
  pointsAttr: string;
  dots: Array<{ x: number; y: number; title: string }>;
}

export const REFRESH_INTERVAL_MS = 15000;
const AUTO_REFRESH_STORAGE_KEY = "agymissioncontrol_auto_refresh";

const FAMILY_COLORS: Record<string, string> = {
  "Gemini 4 Pro": "#0ea5e9",
  "Gemini 4 Argon": "#6366f1",
  "Gemini 4 Flash": "#10b981",
  "Claude / GPT-OSS": "#f59e0b",
};

const DASHBOARD_ICONS = {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Cpu,
  DollarSign,
  Layers,
  Lock,
  PauseCircle,
  Play,
  Radio,
  RefreshCw,
  ShieldAlert,
  Square,
  TrendingDown,
  Unlock,
};

function renderLucideIcons(): void {
  createIcons({ icons: DASHBOARD_ICONS });
}

function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds === null || totalSeconds === undefined || Number.isNaN(totalSeconds)) {
    return "n/a";
  }
  const clamped = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(clamped / 3600);
  const minutes = Math.floor((clamped % 3600) / 60);
  const seconds = clamped % 60;
  if (hours > 0) {
    return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  }
  return `${seconds}s`;
}

function secondsUntilIso(isoTimestamp: string | null | undefined, nowMs: number): number | null {
  if (!isoTimestamp) {
    return null;
  }
  const targetMs = Date.parse(isoTimestamp);
  if (Number.isNaN(targetMs)) {
    return null;
  }
  return Math.max(0, Math.floor((targetMs - nowMs) / 1000));
}

function unwrapArray<T>(payload: unknown, key: string): T[] {
  if (Array.isArray(payload)) {
    return payload as T[];
  }
  if (payload && typeof payload === "object" && Array.isArray((payload as Record<string, unknown>)[key])) {
    return (payload as Record<string, T[]>)[key];
  }
  return [];
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${text}`.trim());
  }
  return (await response.json()) as T;
}

declare global {
  interface Window {
    Alpine: typeof Alpine;
  }
}

function quotaCockpit() {
  return {
    pools: [] as QuotaPoolStatus[],
    history: [] as QuotaHistoryPoint[],
    sessions: [] as SessionRecord[],
    locks: [] as SharedLockRecord[],
    selectedPoolFilter: "ALL",
    autoRefresh: true,
    isRefreshing: false,
    busyKey: "" as string,
    errorMessage: "",
    lastFetchedAt: "Never",
    nowMs: Date.now(),
    pollTimer: null as number | null,
    tickTimer: null as number | null,

    init() {
      const saved = window.localStorage.getItem(AUTO_REFRESH_STORAGE_KEY);
      if (saved === "false" || saved === "0") {
        this.autoRefresh = false;
      }
      this.tickTimer = window.setInterval(() => {
        this.nowMs = Date.now();
      }, 1000);
      if (this.autoRefresh) {
        this.startPolling();
      }
      void this.refreshAll();
    },

    destroy() {
      this.stopPolling();
      if (this.tickTimer !== null) {
        window.clearInterval(this.tickTimer);
        this.tickTimer = null;
      }
    },

    startPolling() {
      this.stopPolling();
      this.pollTimer = window.setInterval(() => {
        void this.refreshAll();
      }, REFRESH_INTERVAL_MS);
    },

    stopPolling() {
      if (this.pollTimer !== null) {
        window.clearInterval(this.pollTimer);
        this.pollTimer = null;
      }
    },

    toggleAutoRefresh() {
      this.autoRefresh = !this.autoRefresh;
      window.localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, String(this.autoRefresh));
      if (this.autoRefresh) {
        this.startPolling();
        void this.refreshAll();
      } else {
        this.stopPolling();
      }
    },

    async refreshAll() {
      if (this.isRefreshing) {
        return;
      }
      this.isRefreshing = true;
      this.errorMessage = "";
      try {
        const [poolsRes, historyRes, sessionsRes, locksRes] = await Promise.all([
          fetchJson<unknown>("/api/quota/status"),
          fetchJson<unknown>("/api/quota/history?limit=240"),
          fetchJson<unknown>("/api/sessions"),
          fetchJson<unknown>("/api/locks"),
        ]);
        this.pools = unwrapArray<QuotaPoolStatus>(poolsRes, "pools");
        this.history = unwrapArray<QuotaHistoryPoint>(historyRes, "points");
        this.sessions = unwrapArray<SessionRecord>(sessionsRes, "sessions");
        this.locks = unwrapArray<SharedLockRecord>(locksRes, "locks");
        this.lastFetchedAt = new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
      } catch (err) {
        this.errorMessage = err instanceof Error ? err.message : String(err);
      } finally {
        this.isRefreshing = false;
        queueMicrotask(renderLucideIcons);
      }
    },

    async postAction(url: string, busyKey: string) {
      this.busyKey = busyKey;
      this.errorMessage = "";
      try {
        await fetchJson<unknown>(url, { method: "POST" });
        await this.refreshAll();
      } catch (err) {
        this.errorMessage = err instanceof Error ? err.message : String(err);
      } finally {
        this.busyKey = "";
        queueMicrotask(renderLucideIcons);
      }
    },

    wakeSession(pid: number) {
      return this.postAction(`/api/wake/${pid}`, `wake-${pid}`);
    },

    stopSession(pid: number) {
      return this.postAction(`/api/stop/${pid}`, `stop-${pid}`);
    },

    wakeAll() {
      return this.postAction("/api/wake-all", "wake-all");
    },

    stopAll() {
      return this.postAction("/api/stop-all", "stop-all");
    },

    clearLock(model: string) {
      return this.postAction(`/api/locks/clear/${encodeURIComponent(model)}`, `lock-${model}`);
    },

    get activeGateCount(): number {
      return this.sessions.filter((s) => s.state === "ACTIVE").length;
    },

    get sleepingGateCount(): number {
      return this.sessions.filter((s) => s.state === "SLEEPING").length;
    },

    get lowestRollingPct(): string {
      const vals = this.pools
        .map((p) => p.rolling_pct)
        .filter((v): v is number => typeof v === "number" && !Number.isNaN(v));
      if (vals.length === 0) {
        return "n/a";
      }
      return `${Math.min(...vals).toFixed(1)}%`;
    },

    get lowestWeeklyPct(): string {
      const vals = this.pools
        .map((p) => p.weekly_pct)
        .filter((v): v is number => typeof v === "number" && !Number.isNaN(v));
      if (vals.length === 0) {
        return "n/a";
      }
      return `${Math.min(...vals).toFixed(1)}%`;
    },

    get totalCreditBurn(): string {
      const total = this.sessions.reduce((acc, s) => acc + (s.credit_burn_usd || 0), 0);
      return `$${total.toFixed(2)}`;
    },

    get availableFamilies(): string[] {
      const set = new Set<string>();
      for (const p of this.pools) {
        set.add(p.model_family);
      }
      for (const h of this.history) {
        set.add(h.model_family);
      }
      return Array.from(set);
    },

    get chartSeries(): ChartSeries[] {
      const filtered = this.history.filter(
        (pt) => this.selectedPoolFilter === "ALL" || pt.model_family === this.selectedPoolFilter,
      );
      if (filtered.length === 0) {
        return [];
      }
      const parsed = filtered
        .map((pt) => ({ pt, ts: Date.parse(pt.timestamp) }))
        .filter((item) => !Number.isNaN(item.ts))
        .sort((a, b) => a.ts - b.ts);
      if (parsed.length === 0) {
        return [];
      }

      const leftPad = 44;
      const rightPad = 780;
      const topPad = 14;
      const bottomPad = 182;
      const minTs = parsed[0].ts;
      const maxTs = parsed[parsed.length - 1].ts;
      const spanTs = Math.max(1, maxTs - minTs);

      const grouped = new Map<string, Array<{ x: number; y: number; title: string }>>();
      for (const { pt, ts } of parsed) {
        const key = `${pt.model_family}::${pt.limit_type}`;
        const x =
          maxTs === minTs
            ? (leftPad + rightPad) / 2
            : leftPad + ((ts - minTs) / spanTs) * (rightPad - leftPad);
        const clampedPct = Math.max(0, Math.min(100, pt.remaining_pct));
        const y = bottomPad - (clampedPct / 100) * (bottomPad - topPad);
        const list = grouped.get(key) ?? [];
        list.push({
          x: Number(x.toFixed(1)),
          y: Number(y.toFixed(1)),
          title: `${pt.model_family} (${pt.limit_type}): ${clampedPct.toFixed(1)}% at ${pt.timestamp}`,
        });
        grouped.set(key, list);
      }

      const series: ChartSeries[] = [];
      for (const [key, dots] of grouped.entries()) {
        const [modelFamily, limitType] = key.split("::");
        series.push({
          key,
          modelFamily,
          limitType,
          color: FAMILY_COLORS[modelFamily] ?? "#38bdf8",
          dashed: limitType.toLowerCase().includes("week"),
          pointsAttr: dots.map((d) => `${d.x},${d.y}`).join(" "),
          dots,
        });
      }
      return series;
    },

    get chartSvgMarkup(): string {
      return this.chartSeries
        .map((series) => {
          const dashAttr = series.dashed ? ' stroke-dasharray="5 3"' : "";
          const dotsMarkup = series.dots
            .map(
              (dot) =>
                `<circle cx="${dot.x}" cy="${dot.y}" r="3" fill="${escapeXml(series.color)}"><title>${escapeXml(dot.title)}</title></circle>`,
            )
            .join("");
          return `<g><polyline fill="none" stroke="${escapeXml(series.color)}" stroke-width="2.2"${dashAttr} points="${escapeXml(series.pointsAttr)}" />${dotsMarkup}</g>`;
        })
        .join("");
    },

    meterFillClass(pct: number | null | undefined, locked: boolean): string {
      if (locked || (pct !== null && pct !== undefined && pct <= 15)) {
        return "fill-depleted";
      }
      if (pct !== null && pct !== undefined && pct <= 45) {
        return "fill-warning";
      }
      return "fill-healthy";
    },

    formatPct(pct: number | null | undefined): string {
      if (pct === null || pct === undefined || Number.isNaN(pct)) {
        return "n/a";
      }
      return `${pct.toFixed(1)}%`;
    },

    countdownFromIso(iso: string | null | undefined): string {
      return formatDuration(secondsUntilIso(iso, this.nowMs));
    },

    sessionRemainingLabel(session: SessionRecord): string {
      if (session.state !== "SLEEPING") {
        return "Running";
      }
      const dynamicSecs = secondsUntilIso(session.wakes_at, this.nowMs);
      if (dynamicSecs !== null) {
        return formatDuration(dynamicSecs);
      }
      return formatDuration(session.remaining_sleep_seconds);
    },
  };
}

window.Alpine = Alpine;
Alpine.data("quotaCockpit", quotaCockpit);
Alpine.start();
queueMicrotask(renderLucideIcons);
