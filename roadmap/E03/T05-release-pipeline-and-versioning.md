# Task E03-T05: Public Open-Source Release Pipeline, Tagged Binaries & Version Pinning

- **Task ID:** `E03-T05`
- **Parent Epic:** [Epic E03: Autonomous Execution Runner, Detached Supervisor & Telemetry (`run`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `infra-specialist`
- **Dependencies:** `E03-T04`

---

## 1. User Story & Strategic Value

> **As an** Engineering Lead distributing tools across multiple repositories and cloud environments,  
> **I want** an automated GitHub Actions release pipeline publishing tagged standalone binaries and Antigravity plugin packages to our public open-source repository,  
> **So that** developers across the community, downstream client repositories, and Antigravity Remote Agent cloud VMs can pin to exact stable versions (`v1.0.0`) with zero runtime dependencies and zero drift, benefiting from public GitHub CI allowances and open collaboration while developers retain local live-reload iteration via `task dev:link`.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `.github/workflows/release.yml` triggers on push of version tags (`v*.*.*`).
- [ ] **AC-2:** Matrix build compiles single-binary executables with PyInstaller across strictly pinned supported runner images:
  - macOS ARM64 (`darwin-arm64`) on `macos-15` (Apple Silicon ARM64)
  - Linux x86_64 (`linux-x86_64`) on `ubuntu-24.04` (Ubuntu 24.04 LTS Noble Numbat), compiled against a stable glibc baseline to guarantee seamless execution inside Google-managed Antigravity Remote Agent cloud VMs.
  - Reject floating `-latest` runner aliases (`ubuntu-latest`, `macos-latest`).
- [ ] **AC-3:** Bundles the Antigravity plugin manifest and skills into a distributable archive `agymissioncontrol-plugin-vX.Y.Z.tar.gz`.
- [ ] **AC-4:** Generates `checksums.txt` containing SHA-256 hashes for all compiled binaries and archives.
- [ ] **AC-5:** Publishes a public GitHub Release using `gh release create` attached with binary assets and changelog on pinned `ubuntu-24.04` runner.
- [ ] **AC-6:** Remote Agent & Clean-Environment Verification: Validates that downloading and running the `linux-x86_64` binary executes cleanly in a minimal container/VM without Python, `uv`, or host developer dependencies installed.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `.github/workflows/release.yml` | Create | Matrix release pipeline building binaries and packaging plugin assets |
| `scripts/package_plugin.py` | Create | Packages `plugin/` directory with version stamping and validation |
| `tests/test_release_assets.py` | Create | Validates artifact packaging, checksum generation, and version flags |

---

## 4. Technical Implementation Specification

### 4.1 GitHub Actions Release Workflow (`.github/workflows/release.yml`)
```yaml
name: Release Pipeline

on:
  push:
    tags:
      - 'v*.*.*'

permissions:
  contents: write

jobs:
  build:
    name: Build Standalone Binaries (${{ matrix.artifact_name }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: macos-15        # Pinned macOS 15 (Apple Silicon ARM64)
            artifact_name: agymissioncontrol-darwin-arm64
          - os: ubuntu-24.04    # Pinned Ubuntu 24.04 LTS (Noble Numbat)
            artifact_name: agymissioncontrol-linux-x86_64
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v7

      - name: Set up uv Toolchain
        uses: astral-sh/setup-uv@v10
        with:
          enable-cache: true
          python-version: "3.15"

      - name: Install pnpm Package Manager
        uses: pnpm/action-setup@v6
        with:
          version: 12

      - name: Set up Node.js LTS
        uses: actions/setup-node@v7
        with:
          node-version: 26  # Node.js 26 Active LTS

      - name: Set up Go-Task Runner
        uses: arduino/setup-task@v3
        with:
          version: 3.x

      - name: Install Dependencies
        run: |
          task install
          task ui:install

      - name: Build Web Cockpit Frontend
        run: task ui:build

      - name: Compile Standalone Executable
        run: task build:binary

      - name: Stage Artifact
        run: mv dist/agymissioncontrol dist/${{ matrix.artifact_name }}

      - name: Upload Binary Artifact
        uses: actions/upload-artifact@v4
        with:
          name: ${{ matrix.artifact_name }}
          path: dist/${{ matrix.artifact_name }}

  publish:
    name: Publish Public Release
    needs: build
    runs-on: ubuntu-24.04       # Pinned Ubuntu 24.04 LTS runner
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v7

      - name: Download Built Artifacts
        uses: actions/download-artifact@v8
        with:
          path: release_assets
          merge-multiple: true

      - name: Package Antigravity Plugin Archive
        run: |
          python3 scripts/package_plugin.py --version ${{ github.ref_name }} --output release_assets

      - name: Generate Checksums & Create Release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          cd release_assets
          chmod +x agymissioncontrol-*
          sha256sum * > checksums.txt
          gh release create ${{ github.ref_name }} * --title "Release ${{ github.ref_name }}" --generate-notes
```

---

## 5. Verification & Definition of Done

- [ ] Dry-run release script builds binary and plugin archive locally.
- [ ] Checksum verification confirms SHA-256 matching.
- [ ] Verify `.github/workflows/release.yml` is pinned strictly to supported OS images (`ubuntu-24.04`, `macos-15`) with no floating `-latest` tags and uses latest pinned actions (`checkout@v7`, `setup-uv@v10`, `action-setup@v6`, `setup-node@v7`, `setup-task@v3`, `upload-artifact@v4`, `download-artifact@v8`).
- [ ] `task check` executes cleanly.
