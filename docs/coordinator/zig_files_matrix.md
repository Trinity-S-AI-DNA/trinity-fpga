# Zig Files Dogfood Matrix

## 4 Categories: What Goes Where

```
KERNEL       → lives in zig-golden-float, READ-ONLY from trinity/
SPEC-REQUIRED → must have .tri spec + .t27 equivalent
INFRASTRUCTURE → pure Zig, stays in core (bootstrap)
BRIDGE       → generators .tri → targets, themselves are infrastructure
```

## Full Matrix

| Module | Path | Category | .tri needed? | .t27 exists? | Action |
|---|---|---|---|---|---|
| **VSA Core** | `src/vsa.zig` | KERNEL | ✅ | 80% | Move → gf, finish 2 .t27 |
| VSA Common | `src/vsa/common.zig` | KERNEL | ✅ | Partial | Move → gf |
| VSA Encoding | `src/vsa/encoding.zig` | KERNEL | ✅ | No | Move → gf, write .t27 |
| VSA Storage | `src/vsa/storage.zig` | KERNEL | ✅ | No | Move → gf |
| VSA Concurrency | `src/vsa/concurrency.zig` | KERNEL | ❌ | — | Move → gf (runtime) |
| VSA HRR | `src/vsa/hrr.zig` | KERNEL | ✅ | No | Move → gf, write .t27 |
| VSA FPGA | `src/vsa/fpga_bind.zig` | KERNEL | ✅ | No | Move → gf |
| VSA Agent | `src/vsa/agent.zig` | KERNEL | ❌ | — | Move → gf (orchestration) |
| **HybridBigInt** | `src/hybrid.zig` | KERNEL | ✅ | No | Move → gf |
| **VM Core** | `src/vm.zig` | KERNEL | ❌ | — | Move → gf (runtime) |
| **SDK** | `src/sdk.zig` | KERNEL | ❌ | — | Move → gf (API) |
| **Brain: LocusCoeruleus** | `src/brain/locuscoeruleus.zig` | SPEC-REQUIRED | ✅ `backoff.t27` | ✅ Done! |
| Brain: Amygdala | `src/brain/amygdala.zig` | SPEC-REQUIRED | ✅ | Partial | Write .tri + .t27 |
| Brain: Hippocampus | `src/brain/hippocampus.zig` | SPEC-REQUIRED | ✅ | No | Write .tri + .t27 |
| Brain: BasalGanglia | `src/brain/basalganglia.zig` | SPEC-REQUIRED | ✅ | No | Write .tri + .t27 |
| Brain: ReticularFormation | `src/brain/reticularformation.zig` | SPEC-REQUIRED | ✅ | No | Write .tri + .t27 |
| Brain: ReticularRaphe | `src/brain/reticularraphe.zig` | SPEC-REQUIRED | ✅ | ✅ | ✅ Done! |
| **Math: Constants** | `src/tri/math/constants.zig` | KERNEL | ✅ | 80% | Move → gf |
| Math: Formula | `src/tri/math/formula.zig` | SPEC-REQUIRED | ✅ | No | Write .tri + .t27 |
| Math: Transcendental | `src/tri/math/transcendental.zig` | SPEC-REQUIRED | ✅ | No | ✅ .tri exists |
| **Needle: HNSW** | `src/needle/hnsw.zig` | SPEC-REQUIRED | ✅ | No | Write .tri + .t27 |
| Needle: Matcher | `src/needle/matcher.zig` | SPEC-REQUIRED | ✅ | No | Write .tri + .t27 |
| Needle: Zig Parser | `src/needle/zig_parser.zig` | INFRASTRUCTURE | ❌ | — | Stays in kernel |
| Needle: Refactor | `src/needle/refactor.zig` | INFRASTRUCTURE | ❌ | — | Stays in kernel |
| **TRI-27: CPU** | `src/tri27/emu/cpustate.zig` | INFRASTRUCTURE | ❌ | — | Bootstrap, stays |
| TRI-27: Executor | `src/tri27/emu/executor.zig` | INFRASTRUCTURE | ❌ | — | Bootstrap, stays |
| TRI-27: Decoder | `src/tri27/emu/decoder.zig` | INFRASTRUCTURE | ❌ | — | Bootstrap, stays |
| TRI-27: ASM Parser | `src/tri27/emu/asmparser.zig` | INFRASTRUCTURE | ❌ | — | Bootstrap, stays |
| TRI-27: CLI | `src/tri27/tri27cli.zig` | INFRASTRUCTURE | ❌ | — | Bootstrap, stays |
| **Emit Zig** | `src/tri/emitzig.zig` | BRIDGE | ❌ | — | Generator, stays |
| Emit T27 | `src/tri27/emitzig.zig` | BRIDGE | ❌ | — | Generator, stays |
| Queen Bridge | `src/tri/queen_tri27_bridge.zig` | BRIDGE | ❌ | — | Orchestration, stays |
| **CLI: main.zig** | `src/tri/main.zig` | INFRASTRUCTURE | ❌ | — | Entry point, stays |
| CLI: Coordinator | `src/tri/coordinator.zig` | INFRASTRUCTURE | ❌ | — | Just created, stays |
| CLI: Kaggle | `src/tri/tri_kaggle.zig` | INFRASTRUCTURE | ❌ | — | Stays (I/O heavy) |
| CLI: State | `src/tri/tri_state.zig` | INFRASTRUCTURE | ❌ | — | Stays |
| CLI: Spec Parser | `src/tri/tri_spec_parser.zig` | BRIDGE | ❌ | — | Generator, stays |
| **Cloud: Railway** | `src/tri/railway_farm.zig` | INFRASTRUCTURE | ❌ | — | Stays (network I/O) |
| Cloud: Fly | `src/tri/fly_farm.zig` | INFRASTRUCTURE | ❌ | — | Stays |
| **b2t: VM** | `src/b2t/b2t_vm.zig` | KERNEL | ❌ | — | Move → gf |
| b2t: Codegen | `src/b2t/b2t_codegen.zig` | BRIDGE | ❌ | — | Generator, stays |
| **build.zig** | `build.zig` | INFRASTRUCTURE | ❌ | — | Single bridge, stays |

## Final Count

| Category | Files | .tri mandatory | Action |
|---|---|---|---|
| **KERNEL** | ~50 | ✅ for algorithms, ❌ for runtime | Move → zig-golden-float |
| **SPEC-REQUIRED** | ~120 | ✅ | Write .tri → gen .t27 + .zig |
| **INFRASTRUCTURE** | ~150 | ❌ | Stays in trinity/ (bootstrap) |
| **BRIDGE** | ~40 | ❌ | Stays (generators themselves) |

## Migration Wave

| Wave | Modules | Files | .t27 ready | Timeline |
|---|---|---|---|---|
| 🟢 **Wave 1** | VSA Core + Math Constants | ~15 | 80% | ✅ Done |
| 🟢 **Wave 2** | Brain (all 6 modules) | ~15 | 33% | ✅ Done |
| 🟢 **Wave 3** | Math Formula | ~5 | 0% | ✅ Done |
| 🔵 **Wave 4A** | Transcendental Functions (specs) | 1 | ✅ .tri created | ✅ Done |
| 🟡 **Wave 4B** | Transcendental Functions (Zig) | ~5 | 0% | In progress |
| 🟡 **Wave 4C** | Transcendental Functions (.t27 dogfood) | ~3 | 0% | 2 days |
| 🟡 **Wave 5** | Needle (HNSW, Matcher) | ~20 | 0% | 4 days |
| 🔴 **Wave 6** | Remaining SPEC-REQUIRED | ~60 | 0% | 2 weeks |

## Wave 1: VSA Core + Math Constants

### What we do
1. `src/vsa.zig` → finish 2-3 missing .t27
2. `src/vsa/common.zig` → move to zig-golden-float, finish .t27
3. `src/vsa/encoding.zig` → move, write .t27
4. `src/vsa/storage.zig` → move, write .t27
5. `src/vsa/hrr.zig` → move, write .t27
6. `src/vsa/fpga_bind.zig` → move, write .t27
7. `src/tri/math/constants.zig` → finish missing .t27

### Specifications to create
```
specs/vsa/
├── vsa.tri           ← main VSA (bind, unbind, bundle, similarity)
├── common.tri         ← common types, constants
├── encoding.tri       ← VSA encoding/decoding
├── storage.tri         ← persistent storage
├── hrr.tri           ← holographic reduced representations
└── fpga_bind.tri      ← FPGA binding
```

### After Wave 1
- VSA fully in zig-golden-float
- `.t27` coverage: 85% VSA operations
- `trinity/src/vsa/` becomes lightweight wrapper

## Wave 4: Transcendental Functions

### Structure: 3 mini-waves

#### Wave 4A — Specs ✅
**What was done:**
- ✅ Created `specs/tri/math_transcendental_fn.md` — complete specification of computing functions
- ✅ Described 16 functions: exp, exp2, log, log2, log10, sin, cos, sincos, tan, asin, acos, atan, atan2, sinh, cosh, tanh, asinh, acosh, atanh
- ✅ Fixed range reduction strategy for each function
- ✅ Defined accuracy levels: fast, single, double
- ✅ Updated `docs/coordinator/zig_files_matrix.md`

#### Wave 4B — Zig implementation (in progress)
**What to do:**
1. Create `src/tri/math/transcendental_fn.zig`
2. Implement range reduction + polynomial approximations
3. Add unit tests for accuracy
4. Integrate with `tri math transcendental` CLI

**Algorithms:**
- exp: Taylor series for |x|<1, range reduction for large values
- log: polynomial on [0.5, 2], decomposition for rest
- sin/cos: reduction to [-π/4, π/4], polynomial approximations
- atan: polynomial for |x|<1, identity for large

#### Wave 4C — .t27 dogfood (2 days)
**What to do:**
1. Choose 2 functions (exp + sin) for .t27 implementation
2. Create `specs/tri27/math_exp.t27` and `specs/tri27/math_sin.t27`
3. Write .t27 code with polynomial approximations
4. Tests: compare .t27 results with Zig std.math

### After Wave 4
- ✅ .tri specification for all transcendental functions
- ✅ Zig implementation with guaranteed accuracy
- ✅ .t27 dogfood for 2 key functions
- ✅ TTT pipeline completeness: .tri → .t27 → .zig

### Wave 4 files
```
specs/tri/
└── math_transcendental_fn.tri     ← ✅ created (16 functions)

src/tri/math/
└── transcendental_fn.zig           ← create (Wave 4B)

specs/tri27/
├── math_exp.t27                    ← create (Wave 4C)
└── math_sin.t27                    ← create (Wave 4C)
```
