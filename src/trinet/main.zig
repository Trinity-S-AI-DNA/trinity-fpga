//! `trinet` — command line for the ternary internet.
//!
//!   trinet selftest              exercise the whole stack and report honestly
//!   trinet probe [serial] [baud] talk to a physical node and verify its receipts
//!   trinet demo [serial]         stand up a mesh, run the agent, print the books
//!   trinet agent "<task>"        run one agent task on a mesh
//!   trinet fleet <s0> [s1] [s2]  run the agent across several physical boards
//!   trinet bench [serial] [n] [baud] [slot]  measure throughput and the transport gap
//!   trinet serve <port> [serial] expose a node over TCP so others can use it
//!   trinet census [serial] [baud] [runs] [jobs]  the distribution, not one good run\n//!   trinet setkey <s0> [s1] [s2] install those keys on the attached boards\n//!   trinet keygen                print fresh per-node receipt keys (never commit them)\n//!   trinet join                  print what a new developer has to do
//!
//! Author: Dmitrii Vasilev (@gHashTag)

const std = @import("std");
const tri_time = @import("tri_time");
const protocol = @import("protocol.zig");
const node_mod = @import("node.zig");
const mesh_mod = @import("mesh.zig");
const ledger_mod = @import("ledger.zig");
const model_mod = @import("model.zig");
const agent_mod = @import("agent.zig");
const net = @import("net.zig");

const default_serial = "/dev/cu.usbserial-1110";
// The historical rate. Measured 2026-08-02: the board's real rate at
// BAUD_DIV=434 is ~164000, so this sits about 2.4% low and works on margin.
const default_baud = 160000;

// What the fleet build ships: BAUD_DIV=60.
//
// Not one board's rate but the midpoint of the fleet's. CFGMCLK is an internal
// RC oscillator and it differs per chip — measured 71.176 MHz on one board and
// 72.065 on another, a 1.25% spread. One host rate has to serve both, so it
// sits between them rather than on either, which halves the worst-case error.
//
// BAUD_DIV=30 was tried first and one board returned 18% of its responses
// damaged while another was clean on the same design and host. A fleet runs at
// the rate every member sustains, not the fastest any member reaches.
const fleet_baud = 1193675;

/// Writer that goes to stderr through std.debug, which is the one output path
/// that is stable across Zig releases.
const Out = struct {
    pub fn print(_: Out, comptime fmt: []const u8, args: anytype) !void {
        std.debug.print(fmt, args);
    }
};
const out: Out = .{};

fn line() void {
    std.debug.print("---------------------------------------------------------------\n", .{});
}

/// Monotonic nanoseconds. Straight onto libc because `tri_time.Timer` is one
/// more thing that moved in 0.16, and a benchmark should not be the place a
/// standard-library rename shows up.
fn monoNanos() u64 {
    var ts: std.c.timespec = undefined;
    _ = std.c.clock_gettime(.MONOTONIC, &ts);
    return @as(u64, @intCast(ts.sec)) * std.time.ns_per_s + @as(u64, @intCast(ts.nsec));
}

/// Zig 0.16 passes the process environment to main rather than exposing it
/// through globals; taking `Init.Minimal` is how a program reads its argv.
pub fn main(init: std.process.Init.Minimal) !void {
    var dbg: std.heap.DebugAllocator(.{}) = .init;
    defer _ = dbg.deinit();
    const gpa = dbg.allocator();

    var arena_state: std.heap.ArenaAllocator = .init(gpa);
    defer arena_state.deinit();
    const args = try init.args.toSlice(arena_state.allocator());

    const cmd = if (args.len > 1) args[1] else "selftest";

    if (std.mem.eql(u8, cmd, "selftest")) return selftest(gpa);
    if (std.mem.eql(u8, cmd, "probe")) return probe(gpa, if (args.len > 2) args[2] else default_serial, if (args.len > 3) try std.fmt.parseInt(u32, args[3], 10) else default_baud);
    if (std.mem.eql(u8, cmd, "demo")) return demo(gpa, if (args.len > 2) args[2] else null);
    if (std.mem.eql(u8, cmd, "agent")) return runAgent(gpa, if (args.len > 2) args[2] else "fix the failing ternary build", if (args.len > 3) args[3] else null);
    if (std.mem.eql(u8, cmd, "bench")) return bench(
        gpa,
        if (args.len > 2) args[2] else default_serial,
        if (args.len > 3) try std.fmt.parseInt(usize, args[3], 10) else 500,
        // 0 means negotiate. The old default was a fleet constant, and a
        // constant is what put the marginal board on the rate that lost it 2.4%
        // of its jobs.
        if (args.len > 4) try std.fmt.parseInt(u32, args[4], 10) else 0,
    );
    if (std.mem.eql(u8, cmd, "fleet")) {
        if (args.len < 3) {
            std.debug.print("usage: trinet fleet <serial0> [serial1] [serial2]\n", .{});
            return error.NoPortsGiven;
        }
        return fleet(gpa, args[2..]);
    }
    if (std.mem.eql(u8, cmd, "serve")) return serve(gpa, args);
    if (std.mem.eql(u8, cmd, "census")) return census(
        gpa,
        if (args.len > 2) args[2] else default_serial,
        if (args.len > 3) try std.fmt.parseInt(u32, args[3], 10) else default_baud,
        if (args.len > 4) try std.fmt.parseInt(usize, args[4], 10) else 100,
        if (args.len > 5) try std.fmt.parseInt(usize, args[5], 10) else 64,
    );
    if (std.mem.eql(u8, cmd, "setkey")) {
        if (args.len < 3) {
            std.debug.print("usage: trinet setkey <serial0> [serial1] [serial2]\n", .{});
            return error.NoPortsGiven;
        }
        return setkey(gpa, args[2..]);
    }
    if (std.mem.eql(u8, cmd, "keygen")) return keygen();
    if (std.mem.eql(u8, cmd, "join")) return joinHelp();

    std.debug.print("unknown command '{s}'\n", .{cmd});
    std.debug.print("try: selftest | probe | bench | census | fleet | keygen | setkey | demo | agent | serve | join\n", .{});
    return error.UnknownCommand;
}

// ---------------------------------------------------------------------------

fn selftest(gpa: std.mem.Allocator) !void {
    std.debug.print("TRI-NET self-test\n", .{});
    line();

    // 1. Protocol agreement with the silicon's own arithmetic.
    const zero_job: protocol.Job = .{ .nonce = .{ 0, 0, 0, 0 }, .w = @splat(0), .x = @splat(0) };
    const tag = protocol.receiptTag(zero_job, 0, protocol.default_node_id);
    std.debug.print("receipt tag for the all-zero job : {x:0>8}", .{tag});
    if (tag == 0xa8fa2bdf) {
        std.debug.print("  matches RTL simulation and Python golden\n", .{});
    } else {
        std.debug.print("  MISMATCH — the three implementations have diverged\n", .{});
        return error.ProtocolDivergence;
    }

    // 2. Policy soundness.
    const policy: ledger_mod.Policy = .{};
    std.debug.print("settlement policy               : reward {d} mTRI/job, slash {d} mTRI, audit {d}%\n", .{ policy.reward_per_job_mtri, policy.slash_per_bad_receipt_mtri, policy.audit_rate_percent });
    std.debug.print("cheating is unprofitable        : {s} (a caught cheat costs {d} jobs of honest work)\n", .{ if (policy.isSound()) "yes" else "NO", policy.slashInJobs() });

    // 3. Adversaries against the verifier.
    line();
    std.debug.print("adversarial nodes vs the verifier\n", .{});
    const behaviours = [_]node_mod.Behaviour{ .honest, .lazy, .replay, .impersonator };
    for (behaviours) |b| {
        var m = try mesh_mod.Mesh.init(gpa, policy);
        defer m.deinit();
        try m.join(node_mod.Node.initEmulated(0xB0, @tagName(b), b), "test", 100000);

        var credited: u64 = 0;
        var caught: u64 = 0;
        for (0..200) |i| {
            var wv: protocol.Trits = @splat(0);
            for (&wv, 0..) |*t, k| t.* = @intCast(@as(i32, @intCast((i + k) % 3)) - 1);
            const job = protocol.Job.withNonce(@intCast(i + 1), protocol.pack(wv), protocol.pack(wv));
            const o = m.dispatch(job) catch break;
            if (o.settlement.outcome == .credited) credited += 1 else caught += 1;
        }
        std.debug.print("  {s:<14} credited {d:>3}/200, rejected {d:>3}\n", .{ @tagName(b), credited, caught });
    }

    line();
    std.debug.print("run `zig test src/trinet/agent.zig -lc` for the full 42-test suite\n", .{});
    std.debug.print("run `trinet probe` to check a physical board\n", .{});
}

// ---------------------------------------------------------------------------

fn probe(gpa: std.mem.Allocator, path: []const u8, baud: u32) !void {
    _ = gpa;
    std.debug.print("probing physical node on {s} at {d} baud\n", .{ path, baud });
    line();

    var buf: [256]u8 = undefined;
    const zpath = try std.fmt.bufPrintZ(&buf, "{s}", .{path});

    var n = node_mod.Node.initFpga(protocol.default_node_id, "ax7203", zpath, baud) catch |e| {
        std.debug.print("could not open the port: {s}\n", .{@errorName(e)});
        std.debug.print("the board may not be attached, or a bitstream that speaks this\n", .{});
        std.debug.print("protocol may not be loaded. Nothing here is a hardware result.\n", .{});
        return e;
    };
    defer n.deinit();

    var ok: usize = 0;
    var fail: usize = 0;
    var stale_key: usize = 0;
    var arith: usize = 0;
    var first_node_id: ?u32 = null;
    var reasons: [8][]const u8 = undefined;
    var n_reasons: usize = 0;

    var prng: std.Random.DefaultPrng = .init(0x7213);
    const rand = prng.random();

    for (0..64) |i| {
        var wv: protocol.Trits = @splat(0);
        var xv: protocol.Trits = @splat(0);
        if (i < 4) {
            // Structural corners first: they catch framing bugs immediately.
            const fills = [_]i8{ 0, 1, -1, 1 };
            for (&wv) |*t| t.* = fills[i];
            for (&xv) |*t| t.* = fills[(i + 1) % 4];
        } else {
            for (&wv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
            for (&xv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
        }
        const job = protocol.Job.withNonce(@intCast(i + 1), protocol.pack(wv), protocol.pack(xv));
        const r = n.execute(job) catch |e| {
            fail += 1;
            if (n_reasons < reasons.len) {
                reasons[n_reasons] = @errorName(e);
                n_reasons += 1;
            }
            continue;
        };
        if (first_node_id == null) first_node_id = r.node_id;
        if (protocol.publishedKeyUsed(job, r) != null) stale_key += 1;
        // The arithmetic and the authenticity are separate questions, and
        // conflating them is how "96 on silicon" came to mean "a serial port
        // opened". A board can compute perfectly and prove nothing.
        if (protocol.statusMeansComputed(r.status) and
            std.mem.eql(u8, &r.nonce, &job.nonce) and
            r.y == protocol.dot(job.w, job.x)) arith += 1;
        const v = protocol.verify(job, r);
        if (v.accepted()) ok += 1 else {
            fail += 1;
            if (n_reasons < reasons.len) {
                reasons[n_reasons] = v.reason();
                n_reasons += 1;
            }
        }
    }

    std.debug.print("dot products correct : {d}/64   (checked against the oracle)\n", .{arith});
    std.debug.print("receipts authenticated: {d}/64  (checked against a key only we should hold)\n", .{ok});
    if (first_node_id) |id| std.debug.print("node id reported : {x:0>8}\n", .{id});
    if (fail > 0) {
        std.debug.print("failures:\n", .{});
        for (reasons[0..n_reasons]) |r| std.debug.print("  {s}\n", .{r});
    }
    line();
    if (stale_key > 0) {
        std.debug.print("PUBLISHED KEY: {d}/64 receipts verified under a key from published_keys.\n", .{stale_key});
        std.debug.print("This board is honest and its arithmetic is fine, but its receipts are\n", .{});
        std.debug.print("evidence of nothing — anyone can compute the same tag from the git\n", .{});
        std.debug.print("history. Re-flash it with a key from `trinet keygen` before crediting\n", .{});
        std.debug.print("any work to it.\n", .{});
        line();
    }
    if (arith == 64 and stale_key == 0 and ok == 64) {
        std.debug.print("RESULT: this is a hardware-measured ternary compute node.\n", .{});
    } else if (stale_key > 0) {
        std.debug.print("RESULT: a working node with worthless receipts. Do not pay it.\n", .{});
        std.debug.print("        Its arithmetic is right {d}/64 times; that part is real.\n", .{arith});
    } else if (arith == 64) {
        std.debug.print("RESULT: the arithmetic is verified on silicon. The receipts are not,\n", .{});
        std.debug.print("        so nothing here establishes which board produced it.\n", .{});
    } else {
        std.debug.print("RESULT: not a verified hardware node. Do not report this as silicon.\n", .{});
    }
}

// ---------------------------------------------------------------------------

/// Node identities are public. Keys are not, and must not be in this file.
///
/// They were, and they were 0x00..0x0f, 0x10..0x1f, 0x20..0x2f — committed to a
/// public repository and guessable even if it had been private. That destroyed
/// the only property the keyed tag bought: a tag anyone can compute is a
/// checksum with extra steps.
///
/// Keys now come from a file the operator generates and does not commit
/// (`trinet keygen` writes one). A node whose key is unknown is still usable —
/// it is registered without one and its receipts are treated as unverifiable
/// rather than as valid.
const FleetNode = struct { name: []const u8, id: u32, key: ?[16]u8 = null };

var fleet_nodes = [_]FleetNode{
    .{ .name = "node0", .id = 0x5452494E },
    .{ .name = "node1", .id = 0x5452494F },
    .{ .name = "node2", .id = 0x54524950 },
};

const key_file_env = "TRINET_KEYS";
const key_file_default = "trinet-keys.txt";

/// The coordinator's journal (journal.zig, gHashTag/trinity-fpga#906). Unset:
/// credits live in memory, as before. Set: paid (node, nonce) pairs and the
/// nonce mark survive a restart, and a credit counts only once it is on disk.
const journal_env = "TRINET_JOURNAL";
/// The handle a board attached by this process is credited to.
const owner_env = "TRINET_OWNER";
/// Who runs this coordinator. Unset, or equal to the owner, writes every
/// credit as self-reported (gHashTag/t27 specs/trinet/node-work-credit.t27).
const operator_env = "TRINET_OPERATOR";

fn envOr(name: [*:0]const u8, default: []const u8) []const u8 {
    const v = std.c.getenv(name) orelse return default;
    const s = std.mem.span(v);
    return if (s.len == 0) default else s;
}

fn ownerHandle() []const u8 {
    return envOr(owner_env, "operator");
}

/// Open TRINET_JOURNAL if it is set. A journal that cannot be read stops the
/// run: crediting without knowing what was already paid is the bug it fixes.
fn openJournalFromEnv(m: *mesh_mod.Mesh) !void {
    const p = std.c.getenv(journal_env) orelse return;
    const path = std.mem.span(p);
    const operator = envOr(operator_env, "");
    const r = m.openJournal(.{ .path = path }, operator) catch |e| {
        std.debug.print("journal {s}: {s} -- not crediting without it\n", .{ path, @errorName(e) });
        switch (e) {
            // Say what it is, because the file is fine to READ and the fix is
            // an operator's decision, not a retry.
            error.JournalTornTail => std.debug.print("The last line was never finished: a previous run died mid-write.\n" ++
                "Appending to it would glue the next line onto the tear, so this run\n" ++
                "stops and the file is left exactly as it is. Stop here and keep the\n" ++
                "file unchanged: it holds the paid set and the nonce reserve mark.\n" ++
                "Recover it only under control, after checking its complete credit\n" ++
                "lines and its last complete mark line.\n", .{}),
            else => {},
        }
        return e;
    };
    std.debug.print("journal {s}: {d} paid jobs restored, nonces resume at {d}, credits {s}\n", .{
        path,
        r.credits,
        m.next_nonce,
        if (operator.len == 0) "self-reported (TRINET_OPERATOR unset)" else "by operator",
    });
}

/// The UART divisor the fleet bitstream is built with. The board's line rate is
/// CFGMCLK / this, so it is also the only honest way to read CFGMCLK back out of
/// a negotiated rate.
const fleet_baud_div: f64 = 60.0;

/// Load per-node keys from `<name> <32 hex chars>` lines.
///
/// Missing file is not an error: the fleet still runs, and every receipt is
/// reported as unverifiable so nobody mistakes an unchecked run for a checked
/// one.
fn loadFleetKeys(gpa: std.mem.Allocator) !usize {
    // std.posix.getenv moved in 0.16; libc's is stable and this is already a
    // libc-linked binary.
    const env_c = std.c.getenv(key_file_env);
    const path: []const u8 = if (env_c) |e| std.mem.span(e) else key_file_default;
    // Straight onto libc, like the serial layer: std.fs moved in 0.16 and a
    // key loader is not the place to chase it.
    var zpath: [512]u8 = undefined;
    const zp = std.fmt.bufPrintZ(&zpath, "{s}", .{path}) catch return 0;
    const fd = std.c.open(zp, .{ .ACCMODE = .RDONLY }, @as(std.c.mode_t, 0));
    if (fd < 0) return 0;
    defer _ = std.c.close(fd);

    const buf = try gpa.alloc(u8, 64 * 1024);
    defer gpa.free(buf);
    const nread = std.c.read(fd, buf.ptr, buf.len);
    if (nread <= 0) return 0;
    const text = buf[0..@intCast(nread)];

    var loaded: usize = 0;
    var lines = std.mem.tokenizeAny(u8, text, "\r\n");
    while (lines.next()) |entry| {
        if (entry.len == 0 or entry[0] == '#') continue;
        var it = std.mem.tokenizeAny(u8, entry, " \t");
        const name = it.next() orelse continue;
        const hex = it.next() orelse continue;
        if (hex.len != 32) continue;
        for (&fleet_nodes) |*n| {
            if (std.mem.eql(u8, n.name, name)) {
                var k: [16]u8 = undefined;
                _ = std.fmt.hexToBytes(&k, hex) catch continue;
                n.key = k;
                loaded += 1;
            }
        }
    }
    return loaded;
}

/// Stand up a mesh of physical boards, one serial port each, and run the agent
/// across it. Every node is real; nothing is filled in with software, and if a
/// board does not answer the report says so rather than quietly shrinking the
/// fleet.
fn fleet(gpa: std.mem.Allocator, ports: []const [:0]const u8) !void {
    std.debug.print("TRI-NET fleet — {d} port(s), line rate negotiated per board\n", .{ports.len});
    const keys_loaded = loadFleetKeys(gpa) catch 0;
    if (keys_loaded == 0) {
        std.debug.print("NO RECEIPT KEYS LOADED (set {s} or create {s} with `trinet keygen`).\n", .{ key_file_env, key_file_default });
        std.debug.print("Receipts below are UNVERIFIABLE — results are checked against the\n", .{});
        std.debug.print("oracle, but nothing establishes who produced them.\n", .{});
    } else {
        std.debug.print("receipt keys loaded for {d} node(s)\n", .{keys_loaded});
    }
    line();

    var m = try mesh_mod.Mesh.init(gpa, .{});
    defer m.deinit();
    try openJournalFromEnv(&m);

    var attached: usize = 0;
    var stale_boards: usize = 0;
    for (ports) |p| {
        // Ask the board who it is rather than inferring it from argument order.
        // Binding identity to position looks fine until the ports come up in a
        // different order — then the coordinator verifies each board against
        // another's key, every honest receipt fails its tag check, and the
        // network slashes operators for a cabling accident.
        // Ask the board its line rate too, rather than assuming one constant
        // serves the fleet. It does not: CFGMCLK is an untrimmed RC oscillator
        // and this fleet's three dies run at 70.46, 67.13 and 68.69 MHz -- a
        // 4.97% spread, and one of them was recorded as a wiring fault for a day
        // while it was answering perfectly 5% down the dial. Each board still
        // tolerates about +/-4.5%, so the windows overlap and one rate does
        // reach all three; ask each board anyway, because that is a fact about
        // these three dies and not about the next one.
        const found = node_mod.Node.initFpgaAutoBaud(0, "unidentified", p) catch |e| {
            std.debug.print("{s}: did NOT answer at any candidate rate ({s}) — not counted\n", .{ p, @errorName(e) });
            continue;
        };
        var n = found.node;
        n.key = @splat(0); // any key; identification only reads the id field
        const probe_job = protocol.Job.withNonce(1, @splat(0), @splat(0));
        const claimed = n.execute(probe_job) catch |e| {
            std.debug.print("{s}: opened but did not answer ({s}) — not counted\n", .{ p, @errorName(e) });
            n.deinit();
            continue;
        };

        var spec: ?FleetNode = null;
        for (fleet_nodes) |f| {
            if (f.id == claimed.node_id) spec = f;
        }
        if (spec == null) {
            std.debug.print("{s}: reports id {x:0>8}, which is not in the fleet table — not counted\n", .{ p, claimed.node_id });
            n.deinit();
            continue;
        }

        n.id = spec.?.id;
        n.name = spec.?.name;
        n.key = spec.?.key;

        // A board flashed with a key from the git history is honest and useless
        // at the same time: its arithmetic is real and its receipts prove
        // nothing, because anyone can compute the same tag. Dropping the key
        // here makes every one of its receipts `unverifiable`, which credits
        // nothing and — just as importantly — slashes nothing.
        if (protocol.publishedKeyUsed(probe_job, claimed) != null) {
            n.key = null;
            stale_boards += 1;
            std.debug.print("{s}: PUBLISHED KEY — receipts carry no evidence. Re-flash with `trinet keygen`.\n", .{p});
        }
        try m.join(n, ownerHandle(), 100000);
        attached += 1;
        std.debug.print("{s}: identified as {s}, id {x:0>8} at {d} baud{s}\n", .{
            p, spec.?.name, spec.?.id, found.baud,
            if (spec.?.key == null) "  (no key — receipts unverifiable)" else "",
        });
    }

    if (attached == 0) {
        std.debug.print("\nno board answered. Nothing below would be a hardware result.\n", .{});
        return error.NoBoardsAttached;
    }
    std.debug.print("\n{d} of {d} requested boards attached\n", .{ attached, ports.len });
    if (stale_boards > 0) {
        std.debug.print("{d} of them still carry a published key, so no work below can be\n", .{stale_boards});
        std.debug.print("credited to them. The dot products are still a hardware measurement.\n", .{});
    }
    std.debug.print("\n", .{});

    const model = try model_mod.Model.synthetic(gpa, 3, 32, 0x1614);
    var agent = try agent_mod.Agent.init(gpa, "igla-coder", model);
    defer agent.deinit(gpa);

    const t_agent = monoNanos();
    const o = agent.run(&m, "synthesise the ternary mac and flash it to the fleet") catch |e| {
        if (e == error.NoEligibleNode and stale_boards == attached) {
            line();
            std.debug.print("Every attached board carries a published key, so the ledger has no\n", .{});
            std.debug.print("node it is allowed to pay and refuses to dispatch. That is the\n", .{});
            std.debug.print("correct behaviour and not a fault in the fleet: {d} boards answered,\n", .{attached});
            std.debug.print("their arithmetic is measurable with `trinet census <port> 0`, and\n", .{});
            std.debug.print("none of it can be settled until they are re-flashed.\n", .{});
            line();
            std.debug.print("Fix: `trinet keygen > trinet-keys.txt`, rebuild each bitstream with\n", .{});
            std.debug.print("its own key via chparam, flash, and run this again.\n", .{});
            return;
        }
        return e;
    };
    const agent_ms = @as(f64, @floatFromInt(monoNanos() - t_agent)) / 1e6;
    std.debug.print("agent action : {s}\n", .{o.decision.action.label()});
    std.debug.print("elapsed      : {d:.1} ms for {d} jobs = {d:.0} jobs/s\n", .{
        agent_ms, o.proof.jobs, @as(f64, @floatFromInt(o.proof.jobs)) / (agent_ms / 1000.0),
    });
    std.debug.print("compute      : {d} jobs, {d} dispatched to serial-attached nodes ({d:.1}%)\n", .{
        o.proof.jobs, o.proof.on_silicon, o.proof.siliconShare() * 100,
    });
    std.debug.print("integrity    : mesh result {s} local recomputation, {d} rows rejected\n\n", .{
        if (o.matches_local) "equals" else "DIFFERS FROM", o.proof.rows_rejected,
    });

    line();
    try m.report(out);
    line();
    if (attached == ports.len and attached > 1) {
        std.debug.print("Every job above was dispatched to a serial-attached board and its\n", .{});
        std.debug.print("answer independently recomputed. That the arithmetic happened in\n", .{});
        std.debug.print("those boards' LUTs is believed, not demonstrated: nothing in a\n", .{});
        std.debug.print("receipt distinguishes a board from software on the same port.\n", .{});
    } else {
        std.debug.print("Fewer boards answered than were asked for; the silicon share above\n", .{});
        std.debug.print("is what actually happened, not what was intended.\n", .{});
    }
}

// ---------------------------------------------------------------------------

/// Measure what one board actually delivers, rather than deriving it.
///
/// The interesting number here is not throughput — it is the ratio between what
/// the transport allows and what the silicon could do. A compute network whose
/// nodes spend all their time waiting on a serial line is a serial line with a
/// compute network attached to it, and the honest way to find that out is to
/// measure both ends and print the gap.
fn bench(gpa: std.mem.Allocator, path: []const u8, n: usize, baud: u32) !void {
    var buf: [256]u8 = undefined;
    const zpath = try std.fmt.bufPrintZ(&buf, "{s}", .{path});

    // bench never loaded the key file. `FleetNode.key` is null until
    // loadFleetKeys fills it, so `verifyWithKey` answered `unverifiable` for
    // every job, `verified` stayed 0, and the throughput line read 0.0 jobs/s
    // whatever the board did — on machines that had the keys all along. The
    // command this project's own next-actions list depends on could not produce
    // a number.
    const keys_loaded = loadFleetKeys(gpa) catch 0;

    var line_rate: u32 = baud;
    var node = blk: {
        if (baud == 0) {
            const found = try node_mod.Node.initFpgaAutoBaud(protocol.default_node_id, "ax7203", zpath);
            line_rate = found.baud;
            break :blk found.node;
        }
        break :blk try node_mod.Node.initFpga(protocol.default_node_id, "ax7203", zpath, baud);
    };
    defer node.deinit();

    // Ask the board who it is. bench used to index the fleet table by a
    // command-line slot, which hands node0's key to whichever port was typed
    // first — the same identity-by-argument-order defect already fixed on the
    // fleet path.
    const who = try node.execute(protocol.Job.withNonce(1, @splat(0), @splat(0)));
    var spec: FleetNode = .{ .name = "unidentified", .id = who.node_id };
    for (fleet_nodes) |f| {
        if (f.id == who.node_id) spec = f;
    }
    node.id = spec.id;
    node.name = spec.name;
    node.key = spec.key;

    std.debug.print("benchmarking {s} (id {x:0>8}) on {s} at {d} baud, {d} jobs\n", .{
        spec.name, spec.id, path, line_rate, n,
    });
    if (spec.key == null) {
        std.debug.print("no key on file for this board ({d} loaded) — throughput below counts\n", .{keys_loaded});
        std.debug.print("jobs that came back WHOLE, not jobs that came back AUTHENTICATED.\n", .{});
    }
    line();

    const latencies = try gpa.alloc(u64, n);
    defer gpa.free(latencies);

    var prng: std.Random.DefaultPrng = .init(0xB3C4);
    const rand = prng.random();

    // Warm up: the first exchange after a flash includes the host opening the
    // port and the FPGA's first frame sync, which is not steady-state.
    for (0..16) |i| {
        var wv: protocol.Trits = @splat(0);
        for (&wv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
        _ = node.execute(protocol.Job.withNonce(@intCast(i), protocol.pack(wv), protocol.pack(wv))) catch {};
    }

    // Two counts, never merged. `verified` is work whose receipt checked out
    // under this board's own key; `whole` is work whose every predictable byte
    // came back right. With a key the first is the number to publish. Without
    // one the second is all there is, and calling it the same thing is how a
    // transport measurement gets cited as authenticated work.
    var verified: usize = 0;
    var whole: usize = 0;
    const t0 = monoNanos();

    for (0..n) |i| {
        var wv: protocol.Trits = @splat(0);
        var xv: protocol.Trits = @splat(0);
        for (&wv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
        for (&xv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
        const job = protocol.Job.withNonce(@intCast(i + 1000), protocol.pack(wv), protocol.pack(xv));

        const start = monoNanos();
        const r = node.execute(job) catch {
            latencies[i] = 0;
            continue;
        };
        const took = monoNanos() - start;
        if (!protocol.statusMeansComputed(r.status)) continue;
        if (!std.mem.eql(u8, &r.nonce, &job.nonce)) continue;
        if (r.y != protocol.dot(job.w, job.x)) continue;
        if (r.node_id != spec.id) continue;
        // Only a job that came back whole has a latency worth reporting. A
        // failure returns fast — that is what failing looks like — and letting
        // it into the percentiles reports the speed of giving up.
        latencies[whole] = took;
        whole += 1;
        if (protocol.verifyWithKey(job, r, node.key).accepted()) verified += 1;
    }

    const elapsed_ns = monoNanos() - t0;

    // Same work again, batched, to separate the line rate from the round trip.
    const batch = 32;
    const jobs = try gpa.alloc(protocol.Job, batch);
    defer gpa.free(jobs);
    const receipts = try gpa.alloc(protocol.Receipt, batch);
    defer gpa.free(receipts);

    var batched_ok: usize = 0;
    var batched_whole: usize = 0;
    const bt0 = monoNanos();
    var done: usize = 0;
    while (done < n) : (done += batch) {
        const take = @min(batch, n - done);
        for (jobs[0..take], 0..) |*j, k| {
            var wv: protocol.Trits = @splat(0);
            var xv: protocol.Trits = @splat(0);
            for (&wv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
            for (&xv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
            j.* = protocol.Job.withNonce(@intCast(5000 + done + k), protocol.pack(wv), protocol.pack(xv));
        }
        const got = node.executeBatch(jobs[0..take], receipts[0..take]) catch break;
        for (jobs[0..got], receipts[0..got]) |j, r| {
            if (!protocol.statusMeansComputed(r.status)) continue;
            if (!std.mem.eql(u8, &r.nonce, &j.nonce)) continue;
            if (r.y != protocol.dot(j.w, j.x)) continue;
            if (r.node_id != spec.id) continue;
            batched_whole += 1;
            if (protocol.verifyWithKey(j, r, node.key).accepted()) batched_ok += 1;
        }
    }
    const batched_ns = monoNanos() - bt0;
    const batched_s = @as(f64, @floatFromInt(batched_ns)) / 1e9;
    const elapsed_s = @as(f64, @floatFromInt(elapsed_ns)) / 1e9;

    // Which count the headline uses, said once, so the label and the arithmetic
    // cannot drift apart.
    const authenticated = spec.key != null;
    const counted = if (authenticated) verified else whole;
    const batched_counted = if (authenticated) batched_ok else batched_whole;
    const basis = if (authenticated) "authenticated" else "whole, NOT authenticated";
    const batched_jps = if (batched_s > 0) @as(f64, @floatFromInt(batched_counted)) / batched_s else 0;

    // Throughput counts VERIFIED jobs, not attempted ones. Dividing by `n` was
    // wrong and hid itself well: a board answering nothing returns instantly,
    // so a total failure read as the fastest run ever recorded. It was caught
    // by the ceiling — 5409 jobs/s against a transport limit of 4942, with
    // 0/64 verified. A rate that counts failures measures how fast you can
    // fail. Every jobs/s figure this project published before 2026-08-02 was
    // computed the broken way and is being restated.
    const jobs_per_s = if (elapsed_s > 0) @as(f64, @floatFromInt(counted)) / elapsed_s else 0;
    const macs_per_s = jobs_per_s * @as(f64, @floatFromInt(protocol.n_trits));

    const lat = latencies[0..whole];
    std.sort.pdq(u64, lat, {}, std.sort.asc(u64));
    const p50 = if (lat.len > 0) lat[lat.len / 2] else 0;
    const p99 = if (lat.len > 0) lat[(lat.len * 99) / 100] else 0;

    // Transport ceiling: 8N1 costs ten bit-times per byte, and UART is full
    // duplex — the request goes out on TX while the response comes back on RX,
    // so the limit is the busier direction, not their sum. Adding them was
    // wrong and showed itself immediately: batched throughput came out at 125%
    // of a ceiling that cannot be exceeded.
    const bytes_per_job: f64 = @floatFromInt(@max(protocol.request_len, protocol.response_len_v2));
    const transport_jobs_per_s = @as(f64, @floatFromInt(line_rate)) / 10.0 / bytes_per_job;

    // Compute ceiling: the dot product is combinational, and the receipt engine
    // walks 26 preimage bytes at one byte per clock, so a job costs roughly 30
    // cycles of the configuration oscillator.
    //
    // CFGMCLK used to be a constant here — 71.18 MHz, a figure taken from one
    // die and applied to a fleet whose three dies measure 70.46, 67.13 and
    // 68.69 MHz. It is not a constant, it is a per-die property of an untrimmed
    // RC oscillator, and the board is telling us what it is: the line rate the
    // link settled on IS CFGMCLK divided by the divisor in the bitstream.
    // Deriving it from the negotiated rate cannot go stale the way a literal
    // does.
    const cfgmclk_hz: f64 = @as(f64, @floatFromInt(line_rate)) * fleet_baud_div;
    const cycles_per_job: f64 = 30.0;
    const compute_jobs_per_s = cfgmclk_hz / cycles_per_job;

    std.debug.print("jobs {s}: {d}/{d}\n", .{ basis, counted, n });
    if (authenticated and whole != verified) {
        std.debug.print("came back whole but unauthenticated: {d}\n", .{whole - verified});
    }
    std.debug.print("elapsed             : {d:.3} s\n", .{elapsed_s});
    std.debug.print("throughput          : {d:.1} jobs/s = {d:.0} ternary MACs/s\n", .{ jobs_per_s, macs_per_s });
    std.debug.print("latency p50 / p99   : {d:.2} ms / {d:.2} ms\n", .{
        @as(f64, @floatFromInt(p50)) / 1e6, @as(f64, @floatFromInt(p99)) / 1e6,
    });
    line();
    std.debug.print("transport ceiling   : {d:.1} jobs/s  (UART {d} baud, {d} bytes on the busier direction)\n", .{
        transport_jobs_per_s, line_rate, @max(protocol.request_len, protocol.response_len_v2),
    });
    std.debug.print("compute ceiling     : {d:.0} jobs/s  (~{d:.0} cycles/job at {d:.2} MHz CFGMCLK,\n", .{
        compute_jobs_per_s, cycles_per_job, cfgmclk_hz / 1e6,
    });
    std.debug.print("                      derived from {d} baud x BAUD_DIV {d:.0}, not assumed)\n", .{
        line_rate, fleet_baud_div,
    });
    std.debug.print("measured / transport: {d:.1}%\n", .{jobs_per_s / transport_jobs_per_s * 100});
    std.debug.print("compute / transport : {d:.0}x\n", .{compute_jobs_per_s / transport_jobs_per_s});

    // A rate above the line rate is not a fast node. It is a broken bench, and
    // saying so here is cheaper than finding it in review.
    if (jobs_per_s > transport_jobs_per_s) {
        line();
        std.debug.print("IMPOSSIBLE: {d:.1} jobs/s exceeds the {d:.1} jobs/s the UART can carry.\n", .{ jobs_per_s, transport_jobs_per_s });
        std.debug.print("The measurement is wrong, not the node. Do not publish this number.\n", .{});
    }
    if (counted == 0) {
        line();
        std.debug.print("NOTHING COUNTED. The throughput and latency above describe failures.\n", .{});
        std.debug.print("Check the rate: `python3 conformance/trinet_baud_sweep.py --port <p>`\n", .{});
        std.debug.print("reports the board's clean window and the rate to use.\n", .{});
    }
    line();
    std.debug.print("batched x{d}       : {d}/{d} {s}, {d:.1} jobs/s ({d:.1}x the one-at-a-time rate)\n", .{
        batch, batched_counted, n, basis, batched_jps, if (jobs_per_s > 0) batched_jps / jobs_per_s else 0,
    });
    std.debug.print("batched / transport : {d:.1}%\n", .{batched_jps / transport_jobs_per_s * 100});
    line();
    std.debug.print("The silicon is idle for all but a fraction of each job. Any\n", .{});
    std.debug.print("throughput claim about this node is a claim about the UART.\n", .{});
    std.debug.print("The compute ceiling above is derived, not measured — measuring it\n", .{});
    std.debug.print("needs a transport that can saturate the cell.\n", .{});
    if (!authenticated) {
        line();
        std.debug.print("These jobs came back whole. No receipt was checked, because no key for\n", .{});
        std.debug.print("this board is on file, so nothing above says WHO did the work. Cite it\n", .{});
        std.debug.print("as a measurement of the transport and not as verified compute.\n", .{});
    }
}

// ---------------------------------------------------------------------------

/// Build a mesh: one physical node when a board answers, plus emulated peers.
/// The emulated peers are labelled as such everywhere, and the report says what
/// fraction of work actually touched silicon.
fn buildMesh(gpa: std.mem.Allocator, serial_path: ?[]const u8, buf: []u8) !mesh_mod.Mesh {
    var m = try mesh_mod.Mesh.init(gpa, .{});
    errdefer m.deinit();
    try openJournalFromEnv(&m);

    if (serial_path) |p| {
        const zpath = try std.fmt.bufPrintZ(buf, "{s}", .{p});
        if (node_mod.Node.initFpga(protocol.default_node_id, "ax7203-node0", zpath, default_baud)) |fpga| {
            try m.join(fpga, ownerHandle(), 100000);
            std.debug.print("node 0: physical AX7203 on {s}\n", .{p});
        } else |e| {
            std.debug.print("node 0: no board ({s}) — running without a physical node\n", .{@errorName(e)});
        }
    }

    try m.join(node_mod.Node.initEmulated(0x4E4F4431, "peer-1", .honest), "developer-1", 100000);
    try m.join(node_mod.Node.initEmulated(0x4E4F4432, "peer-2", .honest), "developer-2", 100000);
    return m;
}

fn demo(gpa: std.mem.Allocator, serial_path: ?[]const u8) !void {
    std.debug.print("TRI-NET demonstration\n", .{});
    line();

    var pathbuf: [256]u8 = undefined;
    var m = try buildMesh(gpa, serial_path orelse default_serial, &pathbuf);
    defer m.deinit();
    std.debug.print("mesh: {d} nodes, {d} physical\n\n", .{ m.nodeCount(), m.physicalCount() });

    const model = try model_mod.Model.synthetic(gpa, 3, 32, 0x1614);
    var agent = try agent_mod.Agent.init(gpa, "igla-coder", model);
    defer agent.deinit(gpa);

    const tasks = [_][]const u8{
        "the gf16 adder conformance test fails on hardware",
        "synthesise the ternary mac and flash it to the board",
        "document the receipt format for new node operators",
    };

    for (tasks) |t| {
        const o = try agent.run(&m, t);
        std.debug.print("task    : {s}\n", .{t});
        std.debug.print("action  : {s} (margin {d:.3} over {s})\n", .{
            o.decision.action.label(),
            o.decision.confidence - o.decision.runner_up_confidence,
            o.decision.runner_up.label(),
        });
        std.debug.print("compute : {d} jobs, {d} on silicon, {d} in software ({d:.1}% hardware)\n", .{
            o.proof.jobs, o.proof.on_silicon, o.proof.in_software, o.proof.siliconShare() * 100,
        });
        std.debug.print("integrity: mesh result {s} local recomputation, {d} rows rejected\n", .{
            if (o.matches_local) "equals" else "DIFFERS FROM", o.proof.rows_rejected,
        });
        std.debug.print("weights : {s}\n\n", .{o.proof.provenance.label()});
    }

    line();
    try m.report(out);
    line();
    std.debug.print("Every number above is measured by this run. The action choices are\n", .{});
    std.debug.print("not meaningful until trained weights are loaded — the arithmetic is.\n", .{});
}

fn runAgent(gpa: std.mem.Allocator, task: []const u8, serial_path: ?[]const u8) !void {
    var pathbuf: [256]u8 = undefined;
    var m = try buildMesh(gpa, serial_path orelse default_serial, &pathbuf);
    defer m.deinit();

    const model = try model_mod.Model.synthetic(gpa, 3, 32, 0x1614);
    var agent = try agent_mod.Agent.init(gpa, "igla-coder", model);
    defer agent.deinit(gpa);

    const o = try agent.run(&m, task);
    std.debug.print("task     : {s}\n", .{task});
    std.debug.print("action   : {s}\n", .{o.decision.action.label()});
    std.debug.print("confident: {s}\n", .{if (o.decision.isConfident()) "yes" else "no"});
    std.debug.print("jobs     : {d} ({d} on silicon)\n", .{ o.proof.jobs, o.proof.on_silicon });
    std.debug.print("credit   : {d} mTRI issued, {d} mTRI slashed\n", .{ o.proof.credit_issued_mtri, o.proof.slashed_mtri });
    std.debug.print("weights  : {s}\n", .{o.proof.provenance.label()});
}

// ---------------------------------------------------------------------------

/// Expose a node over TCP. A developer with a board runs this; a coordinator
/// anywhere on the same overlay can then send it work.
fn serve(gpa: std.mem.Allocator, args: []const [:0]const u8) !void {
    _ = gpa;
    const port: u16 = if (args.len > 2) try std.fmt.parseInt(u16, args[2], 10) else 9701;
    const serial_path: ?[]const u8 = if (args.len > 3) args[3] else null;

    var backing: ?node_mod.Node = null;
    var pathbuf: [256]u8 = undefined;
    if (serial_path) |p| {
        const zpath = try std.fmt.bufPrintZ(&pathbuf, "{s}", .{p});
        backing = node_mod.Node.initFpga(protocol.default_node_id, "ax7203", zpath, default_baud) catch |e| blk: {
            std.debug.print("no board on {s} ({s}); serving in software\n", .{ p, @errorName(e) });
            break :blk null;
        };
    }
    defer if (backing) |*b| b.deinit();

    var software = node_mod.Node.initEmulated(protocol.default_node_id, "software", .honest);

    var l = try net.listen("0.0.0.0", port);
    defer l.close();
    std.debug.print("TRI-NET node listening on port {d} ({s})\n", .{
        port, if (backing != null) "backed by an FPGA" else "software only",
    });

    while (true) {
        var conn = l.accept() catch continue;
        defer conn.close();
        while (true) {
            var raw: [protocol.request_len]u8 = undefined;
            conn.readExact(&raw) catch break;
            if (raw[0] != protocol.magic_req[0] or raw[1] != protocol.magic_req[1]) continue;
            const job: protocol.Job = .{
                .op = raw[2],
                .nonce = raw[3..7].*,
                .w = raw[7..15].*,
                .x = raw[15..23].*,
            };
            const target = if (backing) |*b| b else &software;
            const receipt = target.execute(job) catch protocol.execute(job, target.id);
            conn.writeAll(&protocol.encodeResponse(receipt)) catch break;
        }
    }
}

/// Print fresh per-node keys for the operator to save and build into their own
/// bitstreams. Deliberately prints rather than writes: a key that a tool
/// silently drops in the working tree is a key that gets committed.
/// Run the same measurement many times, each with a fresh port, and report the
/// whole distribution.
///
/// Three consecutive good runs of one configuration is not a statistical base,
/// and a reviewer will say so. What matters is the shape: the worst run, the
/// spread, and how often a run is perfect. A mean alone hides a board that is
/// fine 90% of the time and useless the rest, which is exactly the failure this
/// fleet actually has.
///
/// Every run opens and closes the port. The FPGA's frame parser survives the
/// host process, so a run that inherits a desynchronised cell is a different
/// experiment from one that does not -- and including both is the honest
/// choice, because a user gets whichever they get.
fn census(gpa: std.mem.Allocator, path: []const u8, baud: u32, runs: usize, per_run: usize) !void {
    std.debug.print("census: {d} independent runs of {d} jobs on {s} at {d} baud\n", .{ runs, per_run, path, baud });
    line();

    var buf: [256]u8 = undefined;
    const zpath = try std.fmt.bufPrintZ(&buf, "{s}", .{path});

    const scores = try gpa.alloc(usize, runs);
    defer gpa.free(scores);
    var stale_runs: usize = 0;
    var open_failures: usize = 0;

    // Authenticity is a separate count from arithmetic, and now that a board
    // can hold a key nobody published, it is the one worth reporting. Without
    // a key file the column is simply absent rather than quietly zero.
    const keys_loaded = loadFleetKeys(gpa) catch 0;
    var verified_total: usize = 0;
    var node_key: ?[16]u8 = null;
    var node_name: []const u8 = "unidentified";

    var prng: std.Random.DefaultPrng = .init(0x5EED);
    const rand = prng.random();

    var negotiated: u32 = baud;
    for (0..runs) |run| {
        var n: node_mod.Node = blk: {
            if (negotiated == 0) {
                const found = node_mod.Node.initFpgaAutoBaud(protocol.default_node_id, "ax7203", zpath) catch {
                    scores[run] = 0;
                    open_failures += 1;
                    continue;
                };
                negotiated = found.baud;
                std.debug.print("negotiated line rate: {d} baud\n", .{negotiated});
                break :blk found.node;
            }
            break :blk node_mod.Node.initFpga(protocol.default_node_id, "ax7203", zpath, negotiated) catch {
                scores[run] = 0;
                open_failures += 1;
                continue;
            };
        };
        defer n.deinit();

        var correct: usize = 0;
        var stale: usize = 0;
        for (0..per_run) |i| {
            var wv: protocol.Trits = @splat(0);
            var xv: protocol.Trits = @splat(0);
            for (&wv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
            for (&xv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
            const job = protocol.Job.withNonce(@intCast(run * per_run + i + 1), protocol.pack(wv), protocol.pack(xv));
            const r = n.execute(job) catch continue;
            if (protocol.publishedKeyUsed(job, r) != null) stale += 1;
            if (protocol.statusMeansComputed(r.status) and
                std.mem.eql(u8, &r.nonce, &job.nonce) and
                r.y == protocol.dot(job.w, job.x)) correct += 1;
            if (node_key == null and keys_loaded > 0) {
                for (fleet_nodes) |f| {
                    if (f.id == r.node_id) {
                        node_key = f.key;
                        node_name = f.name;
                    }
                }
            }
            if (node_key) |k| {
                if (protocol.verifyWithKey(job, r, k).accepted()) verified_total += 1;
            }
        }
        scores[run] = correct;
        if (stale > 0) stale_runs += 1;
    }

    var total: usize = 0;
    var perfect: usize = 0;
    for (scores) |c| {
        total += c;
        if (c == per_run) perfect += 1;
    }
    std.sort.pdq(usize, scores, {}, std.sort.asc(usize));

    const attempted = runs * per_run;
    const mean = @as(f64, @floatFromInt(total)) / @as(f64, @floatFromInt(runs));
    std.debug.print("jobs attempted      : {d}\n", .{attempted});
    std.debug.print("dot products correct: {d} ({d:.3}%)\n", .{ total, @as(f64, @floatFromInt(total)) / @as(f64, @floatFromInt(attempted)) * 100 });
    std.debug.print("perfect runs        : {d}/{d}\n", .{ perfect, runs });
    std.debug.print("per-run correct     : min {d}, p50 {d}, p95 {d}, max {d}, mean {d:.2}\n", .{
        scores[0], scores[runs / 2], scores[(runs * 95) / 100], scores[runs - 1], mean,
    });
    if (open_failures > 0) std.debug.print("runs that could not open the port: {d}\n", .{open_failures});
    if (node_key != null) {
        std.debug.print("receipts authenticated: {d} ({d:.3}%) under {s}'s own key\n", .{
            verified_total,
            @as(f64, @floatFromInt(verified_total)) / @as(f64, @floatFromInt(attempted)) * 100,
            node_name,
        });
    } else if (keys_loaded == 0) {
        std.debug.print("receipts authenticated: not checked — no key file loaded\n", .{});
    } else {
        std.debug.print("receipts authenticated: no key on file for this node's id\n", .{});
    }
    line();
    if (stale_runs > 0) {
        std.debug.print("{d}/{d} runs carried receipts signed with a PUBLISHED key.\n", .{ stale_runs, runs });
        std.debug.print("The arithmetic above is a real hardware measurement. The receipts\n", .{});
        std.debug.print("are not evidence of anything and this run must not be cited as\n", .{});
        std.debug.print("verified work.\n", .{});
    } else if (node_key == null) {
        // Reaching here used to print "no published key seen. Receipts from
        // this fleet can be cited." — on a run where no key was loaded, so no
        // key could have been seen, published or otherwise. A green light
        // nothing can turn red is the same defect as a verdict enumeration that
        // treats every unlisted case as innocent: ask what was checked, do not
        // infer it from what failed to happen.
        std.debug.print("no key was checked, so nothing here says who did this work.\n", .{});
        std.debug.print("The arithmetic above is a real hardware measurement. The receipts\n", .{});
        std.debug.print("are unverified and this run must not be cited as verified work.\n", .{});
    } else {
        std.debug.print("no published key seen, and {d}/{d} receipts verified under {s}'s\n", .{
            verified_total, attempted, node_name,
        });
        std.debug.print("own key. This run can be cited as verified work.\n", .{});
    }
    line();
    std.debug.print("Report the minimum, not the mean. A fleet is used at its worst run.\n", .{});
}

/// Install the fleet's keys on the attached boards.
///
/// The key is not baked into the bitstream any more. Re-keying used to mean a
/// place-and-route run the operator's machine cannot perform plus a 13-minute
/// flash per board, and a key that expensive to rotate is a key nobody rotates:
/// the committed-key fix was applied to the source and never reached the
/// silicon, and the fleet ran for a day signing with keys from the git history.
/// Now it costs a power cycle and one 24-byte frame.
fn setkey(gpa: std.mem.Allocator, ports: []const [:0]const u8) !void {
    std.debug.print("installing receipt keys on {d} board(s)\n", .{ports.len});
    line();

    const keys_loaded = loadFleetKeys(gpa) catch 0;
    if (keys_loaded == 0) {
        std.debug.print("No keys to install. Run `trinet keygen > {s}` first —\n", .{key_file_default});
        std.debug.print("and do not commit that file.\n", .{});
        return error.NoKeys;
    }
    std.debug.print("keys loaded for {d} node(s)\n", .{keys_loaded});
    line();

    var installed: usize = 0;
    var locked: usize = 0;
    for (ports) |p| {
        const found = node_mod.Node.initFpgaAutoBaud(0, "unidentified", p) catch |e| {
            std.debug.print("{s}: no answer at any candidate rate ({s})\n", .{ p, @errorName(e) });
            continue;
        };
        var n = found.node;
        defer n.deinit();

        // Ask the board who it is before choosing which key it gets. Binding a
        // key to argument order would hand node0's key to whichever board came
        // up first, and every receipt afterwards would fail its check.
        const who_job = protocol.Job.withNonce(1, @splat(0), @splat(0));
        const claimed = n.execute(who_job) catch |e| {
            std.debug.print("{s}: opened but did not answer ({s})\n", .{ p, @errorName(e) });
            continue;
        };

        var spec: ?FleetNode = null;
        for (fleet_nodes) |f| {
            if (f.id == claimed.node_id) spec = f;
        }
        if (spec == null) {
            std.debug.print("{s}: reports id {x:0>8}, not in the fleet table — skipped\n", .{ p, claimed.node_id });
            continue;
        }
        const key = spec.?.key orelse {
            std.debug.print("{s}: {s} has no key in {s} — skipped\n", .{ p, spec.?.name, key_file_default });
            continue;
        };

        if (claimed.status == protocol.status_ok) {
            std.debug.print("{s}: {s} already holds a key from this configuration.\n", .{ p, spec.?.name });
            std.debug.print("    Power-cycle the board to install a different one.\n", .{});
            locked += 1;
            continue;
        }

        n.setKey(key) catch |e| {
            if (e == error.KeyAlreadySet) {
                std.debug.print("{s}: {s} refused a second key — the latch held, as designed\n", .{ p, spec.?.name });
                locked += 1;
            } else {
                std.debug.print("{s}: {s} did NOT accept the key ({s})\n", .{ p, spec.?.name, @errorName(e) });
            }
            continue;
        };

        // Prove it took, with work rather than with the acknowledgement.
        var ok: usize = 0;
        for (0..32) |i| {
            var wv: protocol.Trits = @splat(0);
            var xv: protocol.Trits = @splat(0);
            for (&wv, 0..) |*t, k| t.* = @intCast(@as(i32, @intCast((i + k) % 3)) - 1);
            for (&xv, 0..) |*t, k| t.* = @intCast(@as(i32, @intCast((i + k + 1) % 3)) - 1);
            const job = protocol.Job.withNonce(@intCast(100 + i), protocol.pack(wv), protocol.pack(xv));
            const r = n.execute(job) catch continue;
            if (protocol.verifyWithKey(job, r, key).accepted()) ok += 1;
        }
        const stale = protocol.publishedKeyUsed(
            protocol.Job.withNonce(1, @splat(0), @splat(0)),
            claimed,
        ) != null;
        std.debug.print("{s}: {s} keyed, {d}/32 receipts verify under the new key{s}\n", .{
            p,                                                           spec.?.name, ok,
            if (stale) "  (was on a PUBLISHED key before this)" else "",
        });
        // The key went in — setKey already checked the acknowledgement's tag,
        // which only a board holding that key can produce. Counting only boards
        // that then scored a perfect 32/32 hid a successfully keyed node behind
        // its own lossy cable, and reported "1 board keyed" when two were.
        installed += 1;
        if (ok < 32) {
            std.debug.print("    {d} of 32 verification jobs did not return clean — that is this\n", .{32 - ok});
            std.debug.print("    board's link, not its key. The key is installed either way.\n", .{});
        }
    }

    line();
    std.debug.print("{d} board(s) keyed, {d} already locked\n", .{ installed, locked });
    if (locked > 0) {
        std.debug.print("A locked board is not a fault: the key is write-once per\n", .{});
        std.debug.print("configuration on purpose, so nobody reaching the wire can replace\n", .{});
        std.debug.print("the operator's key after the fact.\n", .{});
    }
}

fn keygen() !void {
    var seed: u64 = undefined;
    var ts: std.c.timespec = undefined;
    _ = std.c.clock_gettime(.MONOTONIC, &ts);
    seed = @as(u64, @bitCast(@as(i64, ts.nsec))) ^ (@as(u64, @bitCast(@as(i64, ts.sec))) << 20);
    var prng: std.Random.DefaultPrng = .init(seed);
    const rand = prng.random();

    std.debug.print("# TRI-NET receipt keys — save as trinet-keys.txt, NEVER commit\n", .{});
    std.debug.print("# Build each node's bitstream with its own key:\n", .{});
    std.debug.print("#   yosys -p \"... chparam -set RECEIPT_KEY 128'h<key> ...\"\n", .{});
    for (fleet_nodes) |n| {
        var k: [16]u8 = undefined;
        rand.bytes(&k);
        std.debug.print("{s} ", .{n.name});
        for (k) |b| std.debug.print("{x:0>2}", .{b});
        std.debug.print("\n", .{});
    }
    std.debug.print("\nNOTE: this PRNG is seeded from the clock and is fine for a desk\n", .{});
    std.debug.print("fleet, not for anything whose compromise would matter. For that,\n", .{});
    std.debug.print("use `openssl rand -hex 16` per node.\n", .{});
}

fn joinHelp() !void {
    std.debug.print(
        \\Joining TRI-NET
        \\===============
        \\
        \\What you need
        \\  - A Xilinx 7-series board. The reference target is an ALINX AX7203
        \\    (XC7A200T). Anything openXC7 can target will work with a rebuild.
        \\  - A USB serial link to the board and a JTAG programmer.
        \\
        \\Steps
        \\  1. Build the node bitstream, or take the artifact from the
        \\     `AX7203 TRI-NET MAC32 Node` CI workflow.
        \\  2. Pick a node id and rebuild with -DNODE_ID so your node is
        \\     distinguishable. Two nodes sharing an id cannot both be credited.
        \\  3. Flash the board, then run `trinet probe <serial>`. You are a node
        \\     when it reports 64/64 receipts verified.
        \\  4. Run `trinet serve <port> <serial>` and put the machine on the
        \\     coordinator's overlay network.
        \\  5. Register with the coordinator: node id, owner handle, stake.
        \\
        \\What you earn
        \\  Verified ternary compute accrues TRI credit against your handle.
        \\  Credit is an internal work record, not a transferable token: it
        \\  measures contribution so contributors can be paid, and deliberately
        \\  stops short of issuing a financial instrument.
        \\
        \\What loses it
        \\  A receipt that does not verify is slashed against your stake. The
        \\  parameters are set so that a caught cheat costs far more than the
        \\  work it skipped. Run `trinet selftest` to see the adversaries lose.
        \\
    , .{});
}
