# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

`trinity-fpga` is the orchestrator repo for the Trinity project family. It is a Zig-first monorepo that also contains Rust FPGA tooling, Verilog designs, and a Docusaurus docs site.

- **Primary language**: Zig (current toolchain is Zig 0.16.0 in this environment; many modules were written for Zig 0.15.x and are still being migrated).
- **No top-level `build.zig`**: the root `build.zig` was intentionally removed and converted to a `.tri` spec. Individual subprojects still have their own `build.zig` files (e.g., `src/vibeec/build.zig`, `deploy/trinity-nexus/build.zig`).
- **Source of truth**: `.tri` specs in `specs/` and `.trinity/ralph/specs/`. Generated outputs live in `generated/` and `var/trinity/output/` and must not be edited by hand.
- **External kernel**: `external/zig-golden-float` (git submodule) provides the numerical core for GF16, TF3, VSA, and ternary VM operations.
- **FPGA tools**: `rings/` is a Cargo workspace that builds the bitstream flash/verify binary (`trios-fpga`).
- **Compiler**: the VIBEE/Tri compiler sources are in `src/vibeec/`; prebuilt binaries are in `tools/bin/`.

## Common Commands

### Zig

- Check Zig version: `zig version`
- Format a file or directory: `zig fmt src/path/to/file.zig`
- Check formatting without writing: `zig fmt --check src/`
- Run tests for a single file: `zig test src/path/to/file.zig`
- AST check a file: `zig ast-check src/path/to/file.zig`
- Note: `zig build` does **not** work from the repository root because there is no root `build.zig`. Use subproject build files or the `tri` pipeline instead.

### VIBEE / `.tri` Code Generation

- Generate from a spec: `tools/bin/vibee_gen <path/to/spec.tri>` or `tools/bin/vibee_arm64 <path/to/spec.tri>`
- The compiler emits Zig, TRI-27 assembly (`.t27`), Verilog, or Python depending on the spec's `language:` field.
- Generated output is written to `var/trinity/output/` and `generated/`. Edit the spec and regenerate — never edit generated files directly.

### `tri` CLI

- The `tri` CLI entry point is `src/tri/main.zig`. There is no prebuilt `tri` binary at the repo root in this environment; to run it you would need a build step for `src/tri/main.zig` or use an existing build artifact if present.
- Common subcommands referenced throughout the codebase: `tri pipeline run "<task>"`, `tri dev scan`, `tri dev pick --smart`, `tri spec create`, `tri gen`, `tri test`, `tri verdict --toxic`, `tri experience save`, `tri git commit`, `tri agent spawn <issue>`, `tri agent run <issue>`.

### Rust FPGA Workspace (`rings/`)

- Build: `cargo build --workspace`
- Test: `cargo test --workspace`
- Run the bitstream CLI: `cargo run --manifest-path rings/BR-BITSTREAM/Cargo.toml -- --help`
- Flash example: `cargo run --manifest-path rings/BR-BITSTREAM/Cargo.toml -- --xvc-host 192.168.1.30 flash --board XC7A200T --bitstream fpga/vsa/DESIGN.bit`

### FPGA Synthesis (openXC7)

- The canonical FPGA board is an ALINX **AX7203** Artix-7 **XC7A200T-2FBG484**.
- Synthesis uses the `regymm/openxc7` Docker image (amd64; requires QEMU on ARM Macs).
- UART bridge commands referenced in `fpga/README.md`: `tri fpga build-uart`, `tri fpga flash-uart`, `tri fpga uart-test`, `tri fpga status`.
- For detailed board truth, pin mappings, and the full synthesis/flash command sequence, follow `.claude/skills/fpga-synth/SKILL.md`.

### Documentation Site

- Local dev: `cd docs && yarn install && yarn start`
- Build: `cd docs && yarn build`
- `baseUrl` is `/trinity/docs/` — do not change it; it breaks all asset paths.

## High-Level Architecture

### The `.tri` Spec-First Pipeline

The project follows a strict "spec-first" workflow:

```
specs/**/*.tri (VIBEE spec, YAML)
    │
    ├── vibee_gen → *.zig          scaffolding: real signatures, stub bodies
    └── vibee_gen → *.v            Verilog backend
```

**`vibee_gen` does NOT emit `.t27`.** An earlier version of this diagram claimed
it did; there is no t27 emission anywhere in `src/vibeec/`. Nor is t27
"TRI-27 assembly" — it is a high-level language with modules, typed signatures
and `invariant { assert ... }` blocks, compiled by `t27c` (Rust) in a separate
repository.

### Two different languages share the `.tri` extension

| | VIBEE `.tri` | t27 |
|---|---|---|
| syntax | YAML, top-level `name:` | `spec X { }`, `pub fn f(x f32) -> gf16` |
| compiler | `vibee_gen` (Zig, this repo) | `t27c` (Rust, separate repo) |
| backends | Zig, Verilog | Zig, Verilog, HIR, SystemVerilog assertions |
| this repo | 1065 specs | 25 `.t27` files under `specs/numeric/` |

Under `.tri` there were in fact four languages (#792). `python3 tools/spec_dialects.py`
prints the census with vibee_gen's own classifier: 1065 VIBEE YAML, 13 TOML (most are
the `[dna] source` of a cell manifest), 14 t27-style sketches that `t27c` cannot parse,
and 10 that fall through. Markdown documents carry `.md`, and generated output does not
live in `specs/`; CI fails on either (`specs/spec_dialects.t27`).

They are not interchangeable, and each compiler used to accept the other's
files **silently**, producing an empty result and exit 0. `vibee_gen` now
refuses a t27 spec by name and points at `t27c`. Nothing yet stops `t27c`
reading a VIBEE spec, where it parses 835 of our 1137 into an empty module.

Measured, so the split is not mistaken for a plan:

  * `vibee_gen` → Zig: **788 of 1033** specs produce ast-check-clean output,
    with stub bodies
  * `t27c` → Zig: **0 of 9** of its own specs produce valid Zig -- it emits
    invariants and tests but not the declarations they use
  * `t27c` → Verilog: **9 of 9** produce real module structure

So t27's strength today is the hardware path, and VIBEE's is breadth of
scaffolding. Neither produces working software logic yet.

- `src/vibeec/` is the VIBEE compiler: parser, codegen, type checker, bytecode emitter, VM runtime, JIT, Verilog backend.
- `.tri` specs define modules with `name`, `version`, `language`, `module`, `types`, and `behaviors` (each behavior has `given`, `when`, `then`).
- `.vibee` files in `specs/tri/` are the same spec format with a different extension; treat them identically.

### `tri` CLI and the Golden Chain

`src/tri/main.zig` is the unified CLI. It is decomposed into ~650 files covering:

- Brain-zone modules (`src/tri/brain/`, amygdala, hippocampus, cortex, etc.)
- Golden Chain pipeline (`src/tri/golden_chain.zig`, `src/tri/pipeline_executor.zig`)
- Agent orchestration (`src/tri/mu_agent.zig`, `src/tri/swarm/`, `src/tri/cloud_orchestrator.zig`)
- Railway/Deployment helpers (`src/cli/railway_*.zig`)
- FPGA integration (`src/cli/fpga_*.zig`, `src/tri/sacred_fpga.zig`)

The canonical 8/10-step pipeline is:

1. `tri dev scan`
2. `tri dev pick --smart`
3. `tri issue comment N`
4. `tri spec create`
5. `tri gen`
6. `tri test`
7. `tri verdict --toxic`
8. `tri experience save`
9. `tri git commit`
10. `tri loop decide`

### Module Map

| Area | Location | Notes |
|------|----------|-------|
| Ternary VM / VSA core | `src/vsa*.zig`, `src/vm.zig`, `src/hybrid.zig`, `src/sdk.zig` | Allowed direct-edit core library files |
| VIBEE compiler | `src/vibeec/` | Has its own `build.zig` |
| `tri` CLI | `src/tri/` | Entry point `src/tri/main.zig` |
| Agent / orchestration | `src/agent_mu/`, `src/tri/mu_agent.zig` | Issue-bound autonomous agents |
| HSLM training | `src/hslm/` | 74/74 tests required via `zig test src/hslm/model.zig` |
| BSD verification | `src/bsd/` | 3,063,485 Cremona curves verified; read-only LMFDB data in `data/ecdata/` |
| FPGA bitstream tooling | `rings/FP-00`, `rings/FP-01`, `rings/FP-02`, `rings/BR-BITSTREAM` | Cargo workspace |
| Verilog designs | `fpga/openxc7-synth/`, `fpga/vsa/`, `fpga/rtl/` | Synthesized with openXC7 |
| Docs | `docs/` | Docusaurus site |
| Trinity Nexus (experimental) | `deploy/trinity-nexus/` | Separate Zig workspace with core/lang/symb/network/canvas/tools modules |

### Dependency Graph

From `README.md`:

```
t27                    ← SSOT: Ternary specs + Rust bootstrap compiler
         ↑
zig-golden-float      ← Numerical core: GF16, TF3, JIT, VM
         ↑
zig-sacred-geometry     ← Sacred geometry: φ-attention, Beal
zig-physics             ← Quantum: QCD, gravity, dark matter, baryogenesis
zig-hdc                 ← Hyperdimensional: VSA, Sequence HDC
zig-knowledge-graph     ← Knowledge Graph: server + CLI
trinity-training        ← HSLM ML: benchmarks, datasets
         ↑
zig-agents              ← Agents: MCP, autonomous
zig-crypto-mining       ← BTC mining + DePIN
         ↑
trinity                 ← Orchestrator (links all via build.zig.zon)
```

## Important Rules

### Source of Truth

- `.tri` specs are the single source of truth. If a `.tri` spec exists for a module, edit the spec and regenerate — do not hand-edit the generated `.zig`/`.v`/`.py` files.
- Direct `.zig` edits are only allowed for: core library (`src/vsa.zig`, `src/vm.zig`, `src/hybrid.zig`, `src/sdk.zig`), pipeline infrastructure, build system, MCP/bot code, HSLM training, BSD verification, and config files.
- Files in `generated/` and `var/trinity/output/` are read-only.

### Development Workflow

- Use the pipeline: `tri pipeline run "<task>"`. If it fails 3 times, diagnose the pipeline/spec, not the generated code.
- MNL (Mistake → Not-repeat → Learning): a task that failed 3+ consecutive times is considered toxic and should be skipped; re-prioritize based on similar solved tasks.
- Every agent/container must have a `SOUL.md` and be bound to exactly one GitHub issue (canonical registry: `.trinity/issue_bindings.json`).
- Significant agent actions must be reflected as GitHub issue comments using Protocol v2 prefixes (`🔍 [RESEARCH]`, `📜 [SPEC]`, `⚙️ [CODEGEN]`, `🧪 [TEST]`, `☣️ [VERDICT]`, `✅ [DONE]`).

### Code Style & Commits

- Format Zig with `zig fmt` before committing.
- Commit format: `feat(<module>): <description>`, `fix(<module>): ...`, `refactor(<module>): ...`, `docs(<module>): ...`, `chore(<module>): ...`.
- Commit messages must be in English only. Example:
  ```
  feat(cli): add XC7A200T board support

  Added support for the XC7A200T board.
  ```
- Push after commit. Never force-push to `main` without explicit user approval.
- Large files (>1MB) must be in `.gitignore`.

### No Shell Scripts

- Do not create, edit, or reference `.sh`/`.bash` files. Legacy scripts in `scripts/`, `deploy/`, `.ralph/scripts/`, and `fpga/` are marked for deletion.
- Add new tooling as `tri` subcommands or Zig binaries, not shell scripts.
- **One exemption**: `research/benchmark/**/harness/` retains the shell harnesses that produced published measurements. They are evidence, not tooling — never sourced, extended, or copied from, and never included in a sweep that deletes `.sh` files. See `.claude/rules/no-shell-scripts.md`.

### Author Attribution

- Canonical maintainer: **Dmitrii Vasilev** / GitHub **@gHashTag**.
- Do not replace this attribution with generic placeholders in files listed in `tools/config/author_attribution_guard.manifest`.
- `zig build test` in the Nexus subproject runs `src/tri/author_attribution_guard.zig` and fails if attribution is missing.

### VSA / Ternary Conventions

- VSA operations use the trit set `{-1, 0, +1}`. Never mix with binary representations.
- Ternary VM and sacred-geometry constants derive from `φ² + 1/φ² = 3`.

### Zig 0.16 (the toolchain here) — read before editing any .zig file

**Full reference: `.claude/rules/zig-016-migration.md`.** `tri` and seven other
executables build on 0.16; parts of the wider tree still target 0.15.

**Do not "fix" a build error by restoring a 0.15 API** — that undoes migrated
work. Nine stdlib families were removed. Six shim modules restore them, and
they are **build modules, imported by name**, never by relative path:

| use | replaces |
|---|---|
| `@import("tri_time")` | `std.time.timestamp/milli/micro/nanoTimestamp`, `Timer`, `std.Thread.sleep` |
| `@import("tri_env")` | `std.process.getEnvVarOwned/getEnvMap`, `std.posix.getenv` |
| `@import("tri_proc")` | `std.process.Child.run/.init`, `std.process.spawn` |
| `@import("tri_mutex")` | `std.Thread.Mutex`, `std.Thread.RwLock` |
| `@import("tri_rand")` | `std.crypto.random` |
| `@import("tri_io")` | the process `Io`, only where none is in scope |

File I/O is genuinely migrated rather than shimmed: `std.fs.Dir/File` →
`std.Io.Dir/File`, every call takes `io` first. **Prefer an `io` parameter
already in scope over `tri_io.get()`.**

**Always call subprocesses through `tri_proc`.** In 0.16 neither
`std.process.spawn` nor `std.process.run` resolves a bare program name through
PATH, so `.{ "zig", "fmt" }` compiles fine and fails at runtime with
`FileNotFound`. `tri_proc` does the lookup.

Three traps, each of which has cost real time here:

- **`zig ast-check` proves a file PARSES, not that the API exists.** It does
  not resolve members, so an invented function name passes clean — four
  non-existent `std.Io.Dir` functions were written this way. Grep the stdlib
  for `pub fn <name>` before using anything you have not personally seen.
- **`readAll` has no drop-in replacement.** `readStreaming` is one attempt that
  may return 0 without being at EOF and signals EOF by error — the inverse of
  the old contract. Use `Dir.readFile`/`readFileAlloc` for whole files,
  `readSliceAll` where a short read means corruption.
- **Namespace aliases hide work.** `const fs = std.fs;` then `fs.cwd()` is
  invisible to a `std.fs.` grep; 17% of remaining sites are alias-only.

Audit with `zig run tools/api_census.zig -lc` — it resolves aliases, ignores
comments and string literals, and reports which sites block the `tri` binary
specifically rather than the whole tree.

Build one target, not all: `zig build tri-compile`. Bare `zig build` builds
every target and has filled the disk.

### Testing

- Tests live in the same file as source (Zig convention).
- Run a single file: `zig test src/path/to/file.zig`.
- Run the VIBEEC test suite from its subproject: `cd src/vibeec && zig build test`.
- Run HSLM model tests: `zig test src/hslm/model.zig`.
- Run BSD verification: `zig test src/bsd/verify_bsd.zig`.
- Run Rust workspace tests: `cargo test --workspace`.
- Never skip failing tests; fix the root cause.

### FPGA

- Target board: Artix-7 `xc7a200tfbg484-2` (ALINX AX7203).
- Canonical AX7203 constraints: `specs/fpga/constraints/ax7203.xdc`. Every pin in it
  is checked against the package: clock `R4`/`T4` (200 MHz differential,
  `DIFF_SSTL15`, both `MRCC`), LEDs `B13`/`C13`/`D14`/`D15` (bank 16, `LVCMOS18`),
  UART `N15`/`P20`, reset `T6`.
- `fpga/constraints/uart_bridge_j2.xdc` is **not** for this board — its own header says
  QMTech `XC7A100T-1FGG676C`, and the pins it declares (`T23`, `R5`, `T8`, `T9`) do
  not exist in the `fbg484` package at all. The `T23` LED that used to be documented
  here was one of them; the `fbg484` T-row ends at `T21`. Check a pin against
  `prjxray-db/artix7/xc7a200tfbg484-2/package_pins.csv` before trusting any xdc in
  `fpga/`.
- Bank 34 is the DDR3 bank (1.5 V — `DIFF_SSTL15` clock, `LVCMOS15` reset). Of its 50
  pins only `R4`/`T4`/`T6` are accounted for; do not drive the others as outputs
  without the board schematic, or you risk contending with the memory.
- After modifying Verilog, run synthesis via the openXC7 Docker flow or the `/fpga-synth` skill.

## Key Reference Files

- `AGENTS.md` — 27-agent alphabet and Agent T (Queen Trinity) orchestration rules.
- `templates/SOUL.md` — mandatory agent soul template.
- `docs/zig-migration-rules.md` — detailed Zig 0.15 migration notes.
- `graph_v2.json` — module dependency graph used by the orchestrator.
- `.claude/rules/` — detailed per-domain rules (testing, FPGA, specs, HSLM, MCP, docsite, etc.).
- `.cursor/rules/author-attribution-lock.mdc` — author attribution lock for Cursor/Copilot.
- `.github/copilot-instructions.md` — Copilot-specific forbidden-file and toxic-verdict rules.

## Own language first

When this project publishes something about itself, it publishes in **this
project's own language and format** -- not translated into somebody else's.

Owner's rule, 2026-09-20: stop writing in other people's languages, we have our
own.

This bites on any file whose only reason to exist is that an outside tool
expects that shape: `llms.txt`, agents.json, `ai.txt`, `.well-known/*.json`,
A2A agent cards, `ai-plugin` manifests, OpenAPI stubs, JSON-LD blocks, a README
that restates a spec. The reflex is to write four of them in four foreign
formats, and the reflex is wrong: a project whose claim is "here is a language
worth writing" and which then describes itself in three of other people's
formats has published three documents that are not true of it.

**The move:** find the address the outside world already fetches, then serve our
own language at it. `/llms.txt` at t27.ai **is** a t27 module -- `llms.txt`
requires nothing but text, and every prose line of a `.t27` file is a `;`
comment, so it stays readable to anything that cannot compile it.

**Three qualifications, so the rule stays honest:**

- A format a resolver genuinely parses -- a sitemap, `package.json`, a lockfile
  -- is machinery, not a description. **Generate** it from our own source; never
  hand-write it into a second home for the truth.
- Code against someone else's API uses their types. Prose for a human who has
  never heard of the project uses that human's language.
- If a format demands a claim we cannot back, **publish nothing**. An A2A card
  with no A2A server behind it is a false claim, and a missing file is more
  honest than a lying one.

The test: *is this file the project speaking about itself?* If yes, it speaks
our language. If it is plumbing, it speaks the plumbing's.

**Worked example, compiler-checked rather than asserted:**
`gHashTag/trinity/apps/website/specs/catalog/onboarding.t27` generates
`https://t27.ai/llms.txt` and `https://t27.ai/agents.t27` byte-identically
(`gHashTag/trinity/apps/website/public/agents.t27`), gated in CI as
`check:onboarding`. The generator evaluates the spec's own `test` blocks --
`typecheck.ok` stays true for `assert 1 > 2`, so a compiler saying "this parses"
is not a compiler saying "this is true" -- and re-compiles the rendered document
before writing it.

**The full rule lives in exactly one place: the `own-language-first` skill**
(`~/.claude/skills/own-language-first/SKILL.md`). It carries the consent gate for
documents addressed to other people's agents, the six negative controls, and the
`;`-alone-on-a-line trap that silently discards a `module` declaration. This
section is a pointer, not a copy -- the recorded defect in this codebase family
is the hand-copied rule that only two of its three homes knew about.
