//! Generate every .tri/.vibee spec and check the output, as a ratchet.
//!
//!   zig build codegen-corpus              # check against the baseline
//!   zig build codegen-corpus-update       # re-record the baseline
//!
//! Why this exists: the CI gate compiled FOUR specs, and my own sweeps used an
//! arbitrary `head -80` of the tree. The real population is 1137 specs, 1033 of
//! which produce behaviours. Every corpus figure quoted during the generator
//! work -- "24 of 49", "34 of 49" -- came from a ~5% slice, and a regression
//! reached a commit because the failing spec was not in it.
//!
//! Two instruments, deliberately:
//!
//!   ast-check   every spec with behaviours. Fast (about a minute), and it
//!               catches syntax and name-binding regressions across the whole
//!               corpus. This is what the ratchet defends.
//!   compile     `zig test` on a sample, because it is ~3s per spec and the
//!               full set would take 40 minutes. It sees what ast-check cannot
//!               -- call arity, types, members -- which is exactly the class
//!               that slipped through before.
//!
//! phi^2 + 1/phi^2 = 3 = TRINITY

const std = @import("std");
const tri_io = @import("tri_io");
const tri_proc = @import("tri_proc");

const baseline_path = "tools/codegen_corpus_baseline.txt";
const out_dir = ".zig-cache/codegen-corpus";
/// Default number of clean specs to additionally COMPILE. Compiling is ~3s
/// each, so every-push runs stay near a minute; `--compile N` raises it for a
/// nightly. `--compile all` takes about 40 minutes.
///
/// The sample is not the first N: it is spread evenly across the corpus.
/// `ternary_mathematics` failed the four-spec gate through five iterations of
/// a change while a contiguous 24-spec prefix said everything was clean --
/// a prefix samples one directory, not the corpus.
///
/// The sample is drawn from the committed BASELINE, not from this run's clean
/// list. It used to be every 32nd entry of the live list, so one spec counted
/// clean that the baseline did not hold shifted every later pick by one.
/// `specs/tri/phi_utils_multi.tri` sits at baseline index 447, one before the
/// slot at 448, and its output does not compile; all nine codegen-corpus reds
/// on main between 09-24 and 10-04 name it, and commit e6eac0900 was red on
/// push and green on schedule. A sample that moves with the run is a gate
/// whose verdict is not a function of the commit.
const default_compile_sample = 24;

pub fn main(init: std.process.Init.Minimal) !void {
    var gpa_state: std.heap.DebugAllocator(.{}) = .init;
    defer _ = gpa_state.deinit();
    const gpa = gpa_state.allocator();
    const io = tri_io.get();

    var out_buf: [4096]u8 = undefined;
    var stdout = std.Io.File.stdout().writer(io, &out_buf);
    const out = &stdout.interface;
    defer out.flush() catch {};

    const args = try init.args.toSlice(gpa);
    defer gpa.free(args);
    var update = false;
    var gen_arg: ?[]const u8 = null;
    var compile_sample: usize = default_compile_sample;
    var want_compile_arg = false;
    for (args[1..]) |a| {
        if (want_compile_arg) {
            want_compile_arg = false;
            if (std.mem.eql(u8, a, "all")) {
                compile_sample = std.math.maxInt(usize);
            } else {
                compile_sample = std.fmt.parseInt(usize, a, 10) catch default_compile_sample;
            }
        } else if (std.mem.eql(u8, a, "--update")) {
            update = true;
        } else if (std.mem.eql(u8, a, "--compile")) {
            want_compile_arg = true;
        } else if (gen_arg == null) {
            gen_arg = a;
        }
    }

    // The build passes the generator's path as an argument
    // (`addArtifactArg`), which both locates it and makes this step depend on
    // it being built. Searching `.zig-cache` was the first approach and it
    // worked only on a machine that had already built the generator: CI runs
    // this step without building `vibee_gen`, so it failed with
    // `GeneratorNotBuilt` -- a gate that passed locally and could not pass
    // anywhere else.
    const gen = if (gen_arg) |g| try gpa.dupe(u8, g) else try findGenerator(gpa, io);
    defer gpa.free(gen);

    std.Io.Dir.cwd().createDirPath(io, out_dir) catch {};

    var specs: std.ArrayList([]u8) = .empty;
    defer {
        for (specs.items) |s| gpa.free(s);
        specs.deinit(gpa);
    }
    try collectSpecs(gpa, io, "specs", &specs);
    if (specs.items.len == 0) {
        try out.print("no specs found -- refusing to report over an empty set\n", .{});
        return error.EmptyDenominator;
    }
    std.mem.sort([]u8, specs.items, {}, lessThanStr);

    var clean: std.ArrayList([]const u8) = .empty;
    defer clean.deinit(gpa);
    const NoOutput = struct { spec: []const u8, why: []u8 };
    var no_output: std.ArrayList(NoOutput) = .empty;
    defer {
        for (no_output.items) |n| gpa.free(n.why);
        no_output.deinit(gpa);
    }
    var with_behaviours: usize = 0;
    var dirty: usize = 0;

    for (specs.items) |spec| {
        const dest = try outPathFor(gpa, spec);
        defer gpa.free(dest);

        const g = try generate(gpa, io, gen, spec, dest);
        defer if (g.why) |w| gpa.free(w);
        const no_behaviours = g.behaviours == 0;
        if (no_behaviours) continue;
        with_behaviours += 1;

        // The generator prints `Behaviors: N` BEFORE it generates, and when
        // it fails it leaves no file or an empty one. `zig ast-check` on a
        // missing file says "error: unable to open file", which has no
        // ": error: " in it, and it passes an empty file outright, so both
        // used to count as clean.
        const no_usable_file = !g.wrote;
        if (no_usable_file) {
            const why = try gpa.dupe(u8, g.why orelse "(no reason recorded)");
            errdefer gpa.free(why);
            try no_output.append(gpa, .{ .spec = spec, .why = why });
            dirty += 1;
            continue;
        }

        const ast_clean = try astCheckOk(gpa, io, dest);
        if (ast_clean) {
            try clean.append(gpa, spec);
        } else {
            dirty += 1;
        }
    }

    for (no_output.items) |n| {
        std.debug.print("  NO OUTPUT: {s} reported behaviours, then {s}\n", .{ n.spec, n.why });
    }

    if (update) {
        var file = try std.Io.Dir.cwd().createFile(io, baseline_path, .{});
        defer file.close(io);
        var wbuf: [8192]u8 = undefined;
        var w = file.writer(io, &wbuf);
        for (clean.items) |s| try w.interface.print("{s}\n", .{s});
        try w.interface.flush();
        try out.print(
            "baseline written: {d} of {d} specs with behaviours generate ast-check-clean output\n",
            .{ clean.items.len, with_behaviours },
        );
        return;
    }

    const baseline = std.Io.Dir.cwd().readFileAlloc(io, baseline_path, gpa, .limited(4 * 1024 * 1024)) catch null;
    defer if (baseline) |b| gpa.free(b);
    if (baseline == null) {
        try out.print("no baseline yet -- run `zig build codegen-corpus-update`\n", .{});
        return error.NoBaseline;
    }

    var now = std.StringHashMap(void).init(gpa);
    defer now.deinit();
    for (clean.items) |s| try now.put(s, {});

    // The baseline in file order: the compile sample is drawn from it.
    var base: std.ArrayList([]const u8) = .empty;
    defer base.deinit(gpa);
    var in_base = std.StringHashMap(void).init(gpa);
    defer in_base.deinit();
    var lines = std.mem.splitScalar(u8, baseline.?, '\n');
    while (lines.next()) |line| {
        const t = std.mem.trim(u8, line, " \t\r");
        const blank = t.len == 0;
        if (blank) continue;
        try base.append(gpa, t);
        try in_base.put(t, {});
    }

    // A spec whose output USED to be clean and now is not. Regressions only --
    // a spec that was already failing is not this gate's business.
    var regressed: usize = 0;
    for (base.items) |t| {
        const lost = !now.contains(t);
        if (lost) {
            std.debug.print("  REGRESSED: {s} no longer generates ast-check-clean output\n", .{t});
            regressed += 1;
        }
    }

    // Clean now, absent from the baseline. Not a failure -- the ratchet
    // defends only what it recorded -- but named, because an unexplained one
    // is the first thing to look at when two runs of one commit disagree.
    var new_clean: usize = 0;
    for (clean.items) |s| {
        const unrecorded = !in_base.contains(s);
        if (unrecorded) {
            std.debug.print("  NEW CLEAN: {s} is not in the baseline\n", .{s});
            new_clean += 1;
        }
    }

    // The compile sample: what ast-check cannot see. Arity, types, members.
    var compiled: usize = 0;
    var compile_failed: usize = 0;
    const sample = @min(compile_sample, base.items.len);
    // Even stride, not a prefix: the baseline is sorted by path, so the first
    // N are all from one directory. Slot k is baseline entry k * stride, so
    // the picks depend on the commit's baseline and nothing else.
    const stride = if (sample == 0) 1 else base.items.len / sample;
    var picked: std.ArrayList([]const u8) = .empty;
    defer picked.deinit(gpa);
    {
        var k: usize = 0;
        while (k < sample) : (k += 1) {
            const entry = base.items[k * stride];
            // A regressed entry is already a failure above; compiling its
            // output would only report it twice.
            const still_clean = now.contains(entry);
            if (still_clean) try picked.append(gpa, entry);
        }
    }

    std.debug.print(
        \\codegen corpus: {d} of {d} specs with behaviours clean, baseline {d}
        \\  {d} regressed, {d} new clean, {d} reported behaviours and wrote no usable file
        \\  compile sample: {d} slots, stride {d} over the baseline
        \\
    , .{ clean.items.len, with_behaviours, base.items.len, regressed, new_clean, no_output.items.len, sample, stride });

    for (picked.items) |spec| {
        const dest = try outPathFor(gpa, spec);
        defer gpa.free(dest);
        if (try zigTestOk(gpa, io, dest)) {
            compiled += 1;
        } else {
            std.debug.print("  DOES NOT COMPILE: {s} (ast-check passed it)\n", .{spec});
            compile_failed += 1;
        }
    }

    const failed = regressed > 0 or compile_failed > 0;
    if (failed) {
        std.debug.print(
            \\
            \\{d} spec(s) regressed, {d} of {d} sampled outputs do not compile.
            \\
            \\Re-record only if the change is intended:
            \\  zig build codegen-corpus-update
            \\
        , .{ regressed, compile_failed, picked.items.len });
        return error.CodegenCorpusRegressed;
    }

    try out.print(
        \\codegen corpus: {d} of {d} specs with behaviours generate clean output
        \\  no regressions against the baseline
        \\  {d} of those additionally COMPILED (zig test) -- ast-check alone
        \\  cannot see call arity, types or members
        \\
    , .{ clean.items.len, with_behaviours, compiled });
}

fn findGenerator(gpa: std.mem.Allocator, io: std.Io) ![]u8 {
    // The build places it under .zig-cache/o/<hash>/vibee_gen; take the newest.
    const r = try tri_proc.runIo(io, .{
        .allocator = gpa,
        .argv = &.{ "sh", "-c", "find .zig-cache -name vibee_gen -type f -perm +111 -exec ls -t {} + 2>/dev/null | head -1" },
        .max_output_bytes = 4096,
    });
    defer gpa.free(r.stderr);
    errdefer gpa.free(r.stdout);
    const path = std.mem.trim(u8, r.stdout, " \n\r\t");
    if (path.len == 0) {
        gpa.free(r.stdout);
        return error.GeneratorNotBuilt;
    }
    const owned = try gpa.dupe(u8, path);
    gpa.free(r.stdout);
    return owned;
}

/// The output mirrors the spec's path under `out_dir`. It used to flatten
/// '/' to '_', which is not one-to-one: `specs/storm/main.tri` and
/// `specs/storm_main.tri` both became `specs_storm_main.tri.zig`, so the
/// compile sample could test the later spec's output under the earlier
/// spec's name. The generator creates the parent directory itself.
fn outPathFor(gpa: std.mem.Allocator, spec: []const u8) ![]u8 {
    return std.fmt.allocPrint(gpa, "{s}/{s}.zig", .{ out_dir, spec });
}

const Generated = struct {
    /// The count the generator reports, or 0.
    behaviours: usize,
    /// The generator exited 0 and left a non-empty file at `dest`.
    wrote: bool,
    /// When `wrote` is false: the exit status and the generator's last error
    /// line, owned by the caller. "Left no file" without a reason sent the
    /// first reader of this gate's output to rebuild the generator locally.
    why: ?[]u8 = null,
};

fn generate(gpa: std.mem.Allocator, io: std.Io, gen: []const u8, spec: []const u8, dest: []const u8) !Generated {
    // A file left by an earlier run must not stand in for this one.
    std.Io.Dir.cwd().deleteFile(io, dest) catch {};

    const r = tri_proc.runIo(io, .{
        .allocator = gpa,
        .argv = &.{ gen, "gen", spec, dest },
        .max_output_bytes = 256 * 1024,
    }) catch return .{ .behaviours = 0, .wrote = false };
    defer gpa.free(r.stdout);
    defer gpa.free(r.stderr);

    const exited_zero = switch (r.term) {
        .exited => |c| c == 0,
        else => false,
    };
    // The generator exits 0 when it cannot create or write the file, and when
    // it produced 0 bytes (`writeGenerated(...) catch return`), so the exit
    // status alone is not enough.
    const file: FileLeft = blk: {
        const st = std.Io.Dir.cwd().statFile(io, dest, .{}) catch break :blk .none;
        const not_a_file = st.kind != .file;
        if (not_a_file) break :blk .none;
        const is_empty = st.size == 0;
        break :blk if (is_empty) .empty else .nonempty;
    };
    const wrote = exited_zero and file == .nonempty;
    const why: ?[]u8 = if (wrote) null else try describeFailure(gpa, r.term, file, r.stderr);
    return .{
        .behaviours = parseBehaviours(r.stdout, r.stderr),
        .wrote = wrote,
        .why = why,
    };
}

/// What the generator left at the destination. 18 of the 19 aborting specs
/// leave an EMPTY file (probe 37242067946), which is not the same failure as
/// leaving none: the file was created and the write then died.
const FileLeft = enum { none, empty, nonempty };

/// The first words of the line Zig 0.16's crash handlers print before a stack
/// trace (std/debug.zig, the POSIX and Windows segfault handlers, printed by
/// `defaultHandleSegfault`):
/// "Segmentation fault at address 0x...", and so on. Like a panic, these end
/// in SIGABRT, and like a panic they are followed by symbol-less frames.
const crash_handler_lines = [_][]const u8{
    "Segmentation fault",
    "Illegal instruction",
    "Bus error",
    "Arithmetic exception",
    "General protection exception",
    "Stack overflow",
    "Unaligned memory access",
};

/// "signal 6, empty file, thread 9 panic: ..." or "exit 1, no file, error: Foo".
/// The line is the last crash line: one holding "panic: ", or one a crash
/// handler printed. 19 specs abort the generator with SIGABRT (run
/// 37243448031), and an abort's last line is a stack frame with no symbols,
/// "???:?:?: 0x1571044 in ??? (???)", the same for all 19.
/// Failing that, the last line that starts with `error`/`Error` once trimmed:
/// Zig's report of an error returned from main and the generator's write and
/// empty-output messages have that shape, the parser's "  spec error: ..."
/// lines do not. Failing that, the last non-empty line.
fn describeFailure(gpa: std.mem.Allocator, term: tri_proc.Term, file: FileLeft, stderr: []const u8) ![]u8 {
    var status_buf: [32]u8 = undefined;
    const status = switch (term) {
        .exited => |c| try std.fmt.bufPrint(&status_buf, "exit {d}", .{c}),
        .signal => |s| try std.fmt.bufPrint(&status_buf, "signal {d}", .{@intFromEnum(s)}),
        .stopped => |s| try std.fmt.bufPrint(&status_buf, "stopped {d}", .{@intFromEnum(s)}),
        .unknown => |u| try std.fmt.bufPrint(&status_buf, "unknown {d}", .{u}),
    };
    const file_note: []const u8 = switch (file) {
        .none => ", no file",
        .empty => ", empty file",
        .nonempty => "",
    };

    var last_crash: ?[]const u8 = null;
    var last_error: ?[]const u8 = null;
    var last_line: ?[]const u8 = null;
    var it = std.mem.splitScalar(u8, stderr, '\n');
    while (it.next()) |raw| {
        const line = std.mem.trim(u8, raw, " \t\r");
        const blank = line.len == 0;
        if (blank) continue;
        last_line = line;
        const is_panic_line = std.mem.startsWith(u8, line, "panic: ") or std.mem.indexOf(u8, line, " panic: ") != null;
        const is_handler_line = for (crash_handler_lines) |p| {
            const starts = std.mem.startsWith(u8, line, p);
            if (starts) break true;
        } else false;
        const is_crash_line = is_panic_line or is_handler_line;
        if (is_crash_line) last_crash = line;
        const is_error_line = std.mem.startsWith(u8, line, "error") or std.mem.startsWith(u8, line, "Error");
        if (is_error_line) last_error = line;
    }
    const picked = last_crash orelse last_error orelse last_line orelse "(no stderr)";
    const shown = picked[0..@min(picked.len, 200)];
    return std.fmt.allocPrint(gpa, "{s}{s}, {s}", .{ status, file_note, shown });
}

test "describeFailure names the exit status and the last error line" {
    const gpa = std.testing.allocator;
    const stderr =
        \\  spec error: missing field
        \\Generating Verilog...
        \\error: UnsupportedType
        \\/src/vibeec/verilog_codegen.zig:10:5: 0x1 in generate
    ;
    const a = try describeFailure(gpa, .{ .exited = 1 }, .none, stderr);
    defer gpa.free(a);
    try std.testing.expectEqualStrings("exit 1, no file, error: UnsupportedType", a);

    const b = try describeFailure(gpa, .{ .exited = 0 }, .none, "Error: the generator produced 0 bytes for x.zig\n");
    defer gpa.free(b);
    try std.testing.expectEqualStrings("exit 0, no file, Error: the generator produced 0 bytes for x.zig", b);

    const c = try describeFailure(gpa, .{ .exited = 0 }, .none, "");
    defer gpa.free(c);
    try std.testing.expectEqualStrings("exit 0, no file, (no stderr)", c);

    // The panic line and the last frame are the ones runs 37243963781 and
    // 37243448031 printed for the 18 Verilog specs; the error line above them
    // is illustrative. A panic wins over an earlier error line, and an empty
    // file is named as such.
    const aborted =
        \\Generating Verilog...
        \\error: illustrative earlier error
        \\thread 2978 panic: programmer bug caused syscall error: FAULT
        \\???:?:?: 0x1570f10 in ??? (???)
        \\???:?:?: 0x1571044 in ??? (???)
    ;
    const d = try describeFailure(gpa, .{ .signal = .ABRT }, .empty, aborted);
    defer gpa.free(d);
    const want_d = try std.fmt.allocPrint(gpa, "signal {d}, empty file, thread 2978 panic: programmer bug caused syscall error: FAULT", .{@intFromEnum(std.posix.SIG.ABRT)});
    defer gpa.free(want_d);
    try std.testing.expectEqualStrings(want_d, d);

    // A crash handler's line counts as a crash line too. Its wording is
    // std/debug.zig's; the address is illustrative.
    const segv =
        \\error: illustrative earlier error
        \\Segmentation fault at address 0x7f0000001000
        \\???:?:?: 0x1571064 in ??? (???)
    ;
    const e = try describeFailure(gpa, .{ .signal = .ABRT }, .none, segv);
    defer gpa.free(e);
    const want_e = try std.fmt.allocPrint(gpa, "signal {d}, no file, Segmentation fault at address 0x7f0000001000", .{@intFromEnum(std.posix.SIG.ABRT)});
    defer gpa.free(want_e);
    try std.testing.expectEqualStrings(want_e, e);
}

fn parseBehaviours(stdout: []const u8, stderr: []const u8) usize {
    const needle = "Behaviors: ";
    const in_stderr = std.mem.indexOf(u8, stderr, needle) != null;
    const hay = if (in_stderr) stderr else stdout;
    const at = std.mem.indexOf(u8, hay, needle) orelse return 0;
    var i = at + needle.len;
    var n: usize = 0;
    while (i < hay.len and std.ascii.isDigit(hay[i])) : (i += 1) {
        n = n * 10 + (hay[i] - '0');
    }
    return n;
}

fn astCheckOk(gpa: std.mem.Allocator, io: std.Io, path: []const u8) !bool {
    const r = tri_proc.runIo(io, .{
        .allocator = gpa,
        .argv = &.{ "zig", "ast-check", path },
        .max_output_bytes = 256 * 1024,
    }) catch return false;
    defer gpa.free(r.stdout);
    defer gpa.free(r.stderr);
    // Exit status AND the error text. The text alone passed a missing file:
    // "error: unable to open file '...': FileNotFound" exits 1 and has no
    // ": error: " in it.
    const exited_zero = switch (r.term) {
        .exited => |c| c == 0,
        else => false,
    };
    const no_error_line = std.mem.indexOf(u8, r.stderr, ": error: ") == null;
    return exited_zero and no_error_line;
}

test "parseBehaviours reads the count from stderr first, then stdout" {
    try std.testing.expectEqual(@as(usize, 7), parseBehaviours("", "  Types: 2\n  Behaviors: 7\n"));
    try std.testing.expectEqual(@as(usize, 12), parseBehaviours("Behaviors: 12\n", "nothing here"));
    try std.testing.expectEqual(@as(usize, 0), parseBehaviours("", ""));
}

test "outPathFor is one-to-one where flattening was not" {
    const gpa = std.testing.allocator;
    const a = try outPathFor(gpa, "specs/storm/main.tri");
    defer gpa.free(a);
    const b = try outPathFor(gpa, "specs/storm_main.tri");
    defer gpa.free(b);
    try std.testing.expect(!std.mem.eql(u8, a, b));
}

fn zigTestOk(gpa: std.mem.Allocator, io: std.Io, path: []const u8) !bool {
    const r = tri_proc.runIo(io, .{
        .allocator = gpa,
        // A SEPARATE cache dir. This tool runs inside `zig build`, which
        // holds a lock on `.zig-cache`; a child `zig test` using the same
        // directory fails, and `catch return false` reported that as "does
        // not compile". It said 24 of 24 sampled outputs were broken when
        // every one of them compiles and passes by hand.
        // Both cache dirs are passed explicitly.
        //
        // --cache-dir: this tool runs inside `zig build`, which holds a lock
        //   on `.zig-cache`.
        // --global-cache-dir: the child inherits no environment through
        //   tri_proc, so `zig` cannot resolve HOME and fails with
        //   `AppDataDirUnavailable`. That error was invisible behind
        //   `catch return false`, which reported it as "does not compile" for
        //   24 of 24 sampled specs -- every one of which compiles by hand.
        .argv = &.{
            "zig",                "test",
            "--cache-dir",        ".zig-cache/corpus-child",
            "--global-cache-dir", ".zig-cache/corpus-global",
            path,
        },
        .max_output_bytes = 512 * 1024,
    }) catch return false;
    defer gpa.free(r.stdout);
    defer gpa.free(r.stderr);
    return switch (r.term) {
        .exited => |c| c == 0,
        else => false,
    };
}

fn collectSpecs(gpa: std.mem.Allocator, io: std.Io, path: []const u8, acc: *std.ArrayList([]u8)) !void {
    var dir = std.Io.Dir.cwd().openDir(io, path, .{ .iterate = true }) catch return;
    defer dir.close(io);

    var it = dir.iterate();
    while (try it.next(io)) |entry| {
        if (entry.name.len > 0 and entry.name[0] == '.') continue;
        const child = try std.fs.path.join(gpa, &.{ path, entry.name });
        switch (entry.kind) {
            .directory => {
                defer gpa.free(child);
                try collectSpecs(gpa, io, child, acc);
            },
            .file => {
                if (std.mem.endsWith(u8, entry.name, ".tri") or
                    std.mem.endsWith(u8, entry.name, ".vibee"))
                {
                    try acc.append(gpa, child);
                } else gpa.free(child);
            },
            else => gpa.free(child),
        }
    }
}

fn lessThanStr(_: void, a: []u8, b: []u8) bool {
    return std.mem.order(u8, a, b) == .lt;
}
