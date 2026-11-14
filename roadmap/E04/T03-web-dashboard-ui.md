# Task E04-T03: Modern Single-Page Web Cockpit UI (TypeScript, Alpine.js, Lucide Icons, Dark Mode & 15s Auto-Refresh)

- **Task ID:** `E04-T03`
- **Parent Epic:** [Epic E04: Multi-Agent Process Registry & Live Web Cockpit](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🐝 Parallel
- **Primary Agent:** `frontend-developer`
- **Dependencies:** `E04-T01`

---

## 1. User Story & Strategic Value

> **As an** Engineering Lead running multiple background agents,  
> **I want** a modern, dark-themed single-page web cockpit built with TypeScript, Alpine.js, and Lucide icons that surfaces both agent execution and mission control tool overhead (overhead %, cache hit rate, and estimated cost),  
> **So that** I can observe all sleeping and active agents across projects, monitor Mission Control efficiency metrics in real time, and trigger early wakeups with zero runtime dependencies.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1 (Modern Tech Stack):** Frontend source written in TypeScript (`src/agymissioncontrol/ui/frontend/src/main.ts`) using **Alpine.js** for reactive component state and **Lucide** for modern iconography.
- [ ] **AC-2 (Zero Runtime Dependency Delivery):** Pre-compiled via Vite into standalone static assets in `src/agymissioncontrol/ui/static/`, served directly by Python standard library `http.server` without requiring Node.js at runtime.
- [ ] **AC-3 (Dark Mode Theme):** Implements an Antigravity IDE-matching dark theme:
  - Deep background palette (`#0f1117` base, `#1a1d27` cards, `#2d3139` borders).
  - Status accents: Running (Emerald `#10b981`), Sleeping (Amber `#f59e0b` / Indigo `#6366f1`), Completed (Sky `#0ea5e9`), Dead/Stale (Rose `#f43f5e`).
- [ ] **AC-4 (Modern Icons):** Integrates Lucide icons for all UI elements:
  - Header statistics: `Activity`, `Moon`, `Clock`, `Terminal`, `Cpu`, `Coins`, `Percent`.
  - Process cards: `Play`, `Pause`, `Cpu`, `Layers`, `Zap`, `TrendingUp`.
  - Action buttons: `RefreshCw` with rotation animation, `Zap` for early wake.
- [ ] **AC-5 (Tool Overhead & Telemetry Metrics Display):**
  - **Header Summary Card**: Displays Aggregate Active Agents, Sleeping Agents, Mission Control Overhead % (e.g. `9.2%`), Cache Hit Rate (e.g. `74%`), and Total Estimated Cost (`$0.84`).
  - **Process Card Breakdown**: Displays Work Tokens vs. Mission Control Overhead Tokens and Estimated Cost badge.
- [ ] **AC-6 (Manual "Refresh Now" Button):**
  - Instantly fetches `/api/sessions` on click.
  - Triggers a smooth spin animation on the `RefreshCw` icon during fetch.
  - Updates timestamp of last data fetch.
- [ ] **AC-7 (Fixed 15-Second Auto-Refresh Button):**
  - Single button toggle: **"Auto-Refresh (15s)"**.
  - Clicking toggles auto-refresh ON / OFF.
  - Active state rendered with glowing emerald pill/border and status label ("Auto-Refresh: ON (15s)").
  - When enabled, automatically polls `/api/sessions` every fixed 15 seconds (15,000 ms).
  - Preference persisted across reloads in `localStorage`.
- [ ] **AC-8 (Reactive Process Cards):**
  - Renders project name and directory path.
  - Displays current task ID and title.
  - Displays model badge (e.g., `gemini-2.5-flash`, `claude-3-7-sonnet`).
  - Computes and displays live countdown to `wakes_at` timestamp.
  - Provides a **"Wake Early"** action button that dispatches `POST /api/wake/<pid>` and updates state optimistically.
- [ ] **AC-9 (Historical Quota Trend Chart Card):**
  - Fetches time-series data from `GET /api/quota/history`.
  - Renders a clean dark-mode trend chart (SVG line chart / sparklines) plotting authoritative 5-hour rolling and weekly quota percentages over time for each model family.
  - Displays the latest official recovery timestamp badge for each active pool.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/ui/frontend/package.json` | Create | NPM package manifest targeting latest versions (`vite@^8.3.0`, `typescript@^6.0.0`, `alpinejs@^3.17.0`, `@types/alpinejs`, `lucide@^1.72.0`) |
| `src/agymissioncontrol/ui/frontend/tsconfig.json` | Create | TypeScript compiler configuration |
| `src/agymissioncontrol/ui/frontend/vite.config.ts` | Create | Vite build config bundling output to `../static/` |
| `src/agymissioncontrol/ui/frontend/index.html` | Create | Modern dark-mode HTML template with Alpine.js directives |
| `src/agymissioncontrol/ui/frontend/src/main.ts` | Create | TypeScript application entry point, Alpine components & Lucide initialization |
| `src/agymissioncontrol/ui/frontend/src/styles.css` | Create | Dark mode styling, glow utilities, and CSS transitions |
| `src/agymissioncontrol/ui/static/index.html` | Create | Compiled static production asset (shipped with Python package) |
| `tests/test_web_ui.py` | Create | Unit test validating static asset existence and HTML template structure |

---

## 4. Technical Implementation Specification

### 4.1 TypeScript State & Logic (`src/agymissioncontrol/ui/frontend/src/main.ts`)
```typescript
import Alpine from 'alpinejs';
import { createIcons, Activity, Moon, Clock, RefreshCw, Zap, Terminal, Layers, Cpu, Coins, Percent } from 'lucide';

export interface UsageSummary {
  work_tokens: number;
  overhead_tokens: number;
  overhead_pct: number;
  cache_hit_rate: number;
  estimated_cost_usd: number;
}

export interface SessionRecord {
  pid: number;
  project_dir: string;
  project_name: string;
  task_id: string;
  task_title: string;
  model: string;
  state: 'RUNNING' | 'SLEEPING' | 'COMPLETED' | 'STALE';
  wakes_at?: string;
  usage?: UsageSummary;
  updated_at: string;
}

export interface DashboardState {
  sessions: SessionRecord[];
  isRefreshing: boolean;
  autoRefreshEnabled: boolean;
  refreshIntervalId: number | null;
  lastFetchedAt: string;
  readonly REFRESH_INTERVAL_MS: number;
  
  // Computed overhead aggregates
  totalOverheadPct: number;
  totalCostUsd: number;
  avgCacheHitRate: number;

  init(): void;
  fetchSessions(): Promise<void>;
  toggleAutoRefresh(): void;
  wakeSession(pid: number): Promise<void>;
  calculateCountdown(wakesAt?: string): string;
}

const REFRESH_INTERVAL_MS = 15000; // Fixed 15-second interval

Alpine.data('dashboard', (): DashboardState => ({
  sessions: [],
  isRefreshing: false,
  autoRefreshEnabled: false,
  refreshIntervalId: null,
  lastFetchedAt: 'Never',
  REFRESH_INTERVAL_MS,

  get totalOverheadPct(): number {
    let work = 0;
    let overhead = 0;
    for (const s of this.sessions) {
      if (s.usage) {
        work += s.usage.work_tokens;
        overhead += s.usage.overhead_tokens;
      }
    }
    const total = work + overhead;
    return total > 0 ? Math.round((overhead / total) * 1000) / 10 : 0.0;
  },

  get totalCostUsd(): number {
    return Math.round(this.sessions.reduce((acc, s) => acc + (s.usage?.estimated_cost_usd || 0), 0) * 100) / 100;
  },

  get avgCacheHitRate(): number {
    const list = this.sessions.filter(s => s.usage).map(s => s.usage!.cache_hit_rate);
    if (!list.length) return 0.0;
    return Math.round((list.reduce((a, b) => a + b, 0) / list.length) * 10) / 10;
  },

  init() {
    this.fetchSessions();
    const saved = localStorage.getItem('agymissioncontrol_auto_refresh');
    if (saved === 'true') {
      this.toggleAutoRefresh();
    }
  },

  async fetchSessions() {
    this.isRefreshing = true;
    try {
      const res = await fetch('/api/sessions');
      if (res.ok) {
        this.sessions = await res.json();
        this.lastFetchedAt = new Date().toLocaleTimeString();
      }
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
    } finally {
      this.isRefreshing = false;
      this.$nextTick(() => {
        createIcons({ icons: { Activity, Moon, Clock, RefreshCw, Zap, Terminal, Layers, Cpu, Coins, Percent } });
      });
    }
  },

  toggleAutoRefresh() {
    if (this.autoRefreshEnabled) {
      if (this.refreshIntervalId) clearInterval(this.refreshIntervalId);
      this.refreshIntervalId = null;
      this.autoRefreshEnabled = false;
      localStorage.setItem('agymissioncontrol_auto_refresh', 'false');
    } else {
      this.autoRefreshEnabled = true;
      this.refreshIntervalId = window.setInterval(() => this.fetchSessions(), this.REFRESH_INTERVAL_MS);
      localStorage.setItem('agymissioncontrol_auto_refresh', 'true');
    }
  },

  async wakeSession(pid: number) {
    try {
      const res = await fetch(`/api/wake/${pid}`, { method: 'POST' });
      if (res.ok) {
        await this.fetchSessions();
      }
    } catch (err) {
      console.error(`Failed to wake session ${pid}:`, err);
    }
  },

  calculateCountdown(wakesAt?: string): string {
    if (!wakesAt) return 'Ready';
    const target = new Date(wakesAt).getTime();
    const diff = Math.max(0, target - Date.now());
    const mins = Math.floor(diff / 60000);
    const secs = Math.floor((diff % 60000) / 1000);
    return `${mins}m ${secs}s`;
  }
}));

Alpine.start();
createIcons({ icons: { Activity, Moon, Clock, RefreshCw, Zap, Terminal, Layers, Cpu, Coins, Percent } });
```

---

## 5. Verification & Definition of Done

- [ ] `task ui:build` completes without error, generating compiled bundle in `src/agymissioncontrol/ui/static/`.
- [ ] Unit test in `tests/test_web_ui.py` confirms presence of `src/agymissioncontrol/ui/static/index.html` and verifies bundled HTML contains Alpine.js, Lucide, and overhead metric references.
- [ ] Manual browser test verifies Dark Mode theme displays overhead summary cards (`Overhead %`, `Cache %`, `Cost`).
- [ ] Manual test verifies clicking "Refresh Now" spins the icon and updates data immediately.
- [ ] Manual test verifies clicking "Auto-Refresh: OFF (15s)" toggles state to "Auto-Refresh: ON (15s)" and polls every 15 seconds.
- [ ] `task check` executes cleanly.
