# Pins DSL — Migration Plan

## Current Status

**Implementation:** `src/tri/pins_parser.zig` (Bootstrap Zig, 1100+ LOC)
**Status:** ✅ Parsing & XDC generation working
**Blocked by:** TVM string/file I/O in VIBEE — cannot emit .tri

## Migration Path

The Pins DSL is currently implemented as **bootstrap Zig** in `src/tri/pins_parser.zig`. This violates the Trinity architecture where:

- ✅ **Source of Truth** should be `.tri` specifications (e.g., `specs/algo/relu.tri`)
- ✅ **Compilation** should happen through VIBEE (`.tri` → `.t27` → TVM)
- ✅ **All Zig** lives in `zig-golden-float` (VM, runtime, utilities)

### Problem

The current implementation **writes Zig directly** (`pins_parser.zig`) which should be `.tri` specs that get compiled to `.t27` then emitted through TVM.

## Migration Steps

### Phase 1: Create `.tri` Specs

Create proper `.tri` specifications for each module in `pins_parser.zig`:

1. **`specs/pins/parser.tri`** — Lexer, Parser, AST
   - Define token types, grammar rules
   - Parse `.pins.tri` files to AST

2. **`specs/pins/validator.md`** — Validation rules
   - Duplicate LOC detection
   - Missing bind checks
   - Orphaned signal detection
   - Pin direction conflicts

3. **`specs/pins/emitter_xdc.md`** — XDC generation
   - Generate `.xdc` constraints from design

4. **`specs/pins/emitter_pcf.tri`** — PCF generation
   - Generate `.pcf` timing constraints

5. **`specs/pins/emitter_ir.tri`** — IR export
   - JSON intermediate representation

### Phase 2: Update `tri` CLI

Modify `src/tri/tri/tri_fpga.zig` to:

1. Call `.t27` modules via TVM string/file I/O
   - Remove direct Zig implementation calls
   - Use `tri gen specs/pins/*.tri` command
   - Output formatting from `.t27` results

### Phase 3: Deprecate Bootstrap Zig

Mark `src/tri/pins_parser.zig` as **DEPRECATED**:

```zig
// BOOTSTRAP: Migration to .tri specs in progress
// This file is bootstrap implementation pending migration to proper .tri specs.
// See: specs/pins/README.md for migration plan.
//
```

### Success Criteria

Migration is complete when:

- [ ] All parser logic moved to `.tri` specs
- [ ] All validation rules in `.tri` specs
- [ ] All emitter logic in `.tri` specs
- [ ] `tri_fpga.zig` uses `.tri gen` instead of direct calls
- [ ] `tri fpga pins validate` works via `.tri` specs
- [ ] `tri fpga pins doctor` works via `.tri` specs
- [ ] Build passes with 0 warnings
- [ ] Bootstrap Zig marked as DEPRECATED

## Next Steps

1. Start implementing `specs/pins/parser.tri`
2. Test `tri gen` on parser specs
3. Update CLI to use `.tri gen`
4. Deprecate `pins_parser.zig`
5. Update README with progress

---

**NOTE:** This is an architectural refactoring, not a bug fix. The current `pins_parser.zig` works correctly for its purpose, but doesn't follow Trinity's `.tri` specification pattern.
