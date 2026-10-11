//! The TRI-NET coordinator — hands ternary jobs to nodes, judges what comes
//! back, and settles it.
//!
//! Three things are kept deliberately separate, because collapsing them is how
//! compute networks end up paying for work that never happened:
//!
//!   node.execute   produces an untrusted claim
//!   protocol.verify judges the claim against an independent recomputation
//!   ledger.settle  moves credit, and only ever on a judged claim
//!
//! The coordinator also carries the network's honesty counters: how many jobs
//! ran on real silicon versus software. A network that says it is hardware
//! compute should be able to answer that question from its own books.
//!
//! Author: Dmitrii Vasilev (@gHashTag)

const std = @import("std");
const protocol = @import("protocol.zig");
const node_mod = @import("node.zig");
const ledger_mod = @import("ledger.zig");
const journal_mod = @import("journal.zig");

pub const Node = node_mod.Node;
pub const Ledger = ledger_mod.Ledger;

/// Jobs handed to one node per round trip. 32 is a model layer's width here, so
/// a layer is naturally one batch, and it is what the throughput measurement
/// was taken at.
pub const max_batch = 32;

pub const Error = error{
    NoEligibleNode,
    OutOfMemory,
    UnknownNode,
    DuplicateNode,
    InsufficientStake,
    InvalidOwner,
    UnsoundPolicy,
    /// The journal could not take the next nonce mark, so no nonce is issued.
    JournalWrite,
    JournalUnavailable,
    /// The 32-bit nonce space is spent; nodes need a new key epoch.
    NonceSpaceExhausted,
};

pub const JobOutcome = struct {
    node_id: u32,
    node_name: []const u8,
    physical: bool,
    y: i8,
    verdict: protocol.Verdict,
    settlement: ledger_mod.Settlement,
    /// Set when the node could not be reached at all.
    unreachable_node: bool = false,
};

pub const QuorumOutcome = struct {
    /// Value agreed by a strict majority of responding nodes, if any.
    agreed: ?i8,
    responses: u32,
    agreeing: u32,
    /// True when the majority answer is also the mathematically correct one.
    /// Recorded rather than assumed: a quorum protects against a minority of
    /// liars, not against a majority of them.
    majority_was_correct: bool,
};

pub const Stats = struct {
    dispatched: u64 = 0,
    accepted: u64 = 0,
    rejected: u64 = 0,
    unreachable_jobs: u64 = 0,
    /// Damaged frames. Separated from rejections because one is a statement
    /// about the operator's wiring and the other about their honesty.
    corrupt_jobs: u64 = 0,
    unverifiable_jobs: u64 = 0,
    not_eligible_jobs: u64 = 0,
    /// Verified but not put on disk, or a nonce this run did not issue.
    not_recorded_jobs: u64 = 0,
    on_silicon: u64 = 0,
    in_software: u64 = 0,

    /// Share of jobs dispatched to a SERIAL-ATTACHED node.
    ///
    /// This is a transport-type label, not a measurement of where arithmetic
    /// happened. Node.isPhysical() reports whether the backend is a serial
    /// port, decided when the port opened; nothing in the job path carries
    /// evidence of hardware origin, and software on the other end of that port
    /// would be indistinguishable. Do not report it as "on silicon".
    pub fn siliconShare(self: Stats) f64 {
        const total = self.on_silicon + self.in_software;
        if (total == 0) return 0;
        return @as(f64, @floatFromInt(self.on_silicon)) / @as(f64, @floatFromInt(total));
    }
};

pub const Mesh = struct {
    gpa: std.mem.Allocator,
    nodes: std.ArrayList(Node) = .empty,
    ledger: Ledger,
    stats: Stats = .{},
    cursor: usize = 0,
    next_nonce: u32 = 1,
    /// With a journal: the highest nonce mark on disk, and where this run
    /// resumed. Nonces are issued only in [resumed_from, nonce_mark).
    nonce_mark: u32 = 0,
    resumed_from: u32 = 1,

    pub fn init(gpa: std.mem.Allocator, policy: ledger_mod.Policy) Error!Mesh {
        return .{ .gpa = gpa, .ledger = try Ledger.init(gpa, policy) };
    }

    pub fn deinit(self: *Mesh) void {
        for (self.nodes.items) |*n| n.deinit();
        self.nodes.deinit(self.gpa);
        self.ledger.deinit();
    }

    /// Attach a node and bond its owner's stake. This is the join step a new
    /// developer performs; everything after it is automatic.
    pub fn join(self: *Mesh, n: Node, owner: []const u8, stake_mtri: u64) Error!void {
        try self.ledger.register(n.id, owner, n.isPhysical(), stake_mtri);
        try self.nodes.append(self.gpa, n);
    }

    pub fn nodeCount(self: Mesh) usize {
        return self.nodes.items.len;
    }

    pub fn physicalCount(self: Mesh) usize {
        var k: usize = 0;
        for (self.nodes.items) |n| {
            if (n.isPhysical()) k += 1;
        }
        return k;
    }

    /// Open the coordinator's journal (journal.zig): rebuild what was paid and
    /// resume nonces at the last mark, so a restart never reissues a nonce.
    pub fn openJournal(self: *Mesh, j: journal_mod.Journal, operator: []const u8) journal_mod.Error!journal_mod.Restored {
        const r = try self.ledger.openJournal(j, operator);
        self.nonce_mark = r.mark;
        self.next_nonce = if (r.mark == 0) 1 else r.mark;
        self.resumed_from = self.next_nonce;
        return r;
    }

    pub fn freshNonce(self: *Mesh) Error!u32 {
        if (self.ledger.journal) |j| {
            // Reserve before issuing: the mark is on disk before any nonce at
            // or above the old mark goes out.
            if (self.next_nonce >= self.nonce_mark) {
                const mark = journal_mod.nextMark(self.nonce_mark);
                if (mark == 0 or mark <= self.next_nonce) return Error.NonceSpaceExhausted;
                j.writeMark(mark) catch return Error.JournalWrite;
                self.nonce_mark = mark;
            }
        }
        const n = self.next_nonce;
        self.next_nonce +%= 1;
        return n;
    }

    /// With a journal, only a nonce this run issued may be credited.
    fn issuedByThisRun(self: *Mesh, nonce: u32) bool {
        if (self.ledger.journal == null) return true;
        return nonce >= self.resumed_from and nonce < self.next_nonce;
    }

    fn pickEligible(self: *Mesh) ?*Node {
        const count = self.nodes.items.len;
        if (count == 0) return null;
        var tried: usize = 0;
        while (tried < count) : (tried += 1) {
            const idx = (self.cursor + tried) % count;
            const candidate = &self.nodes.items[idx];
            if (self.ledger.isEligible(candidate.id)) {
                self.cursor = (idx + 1) % count;
                return candidate;
            }
        }
        return null;
    }

    /// Send one job to the next eligible node, judge the answer, settle it.
    pub fn dispatch(self: *Mesh, job: protocol.Job) Error!JobOutcome {
        const n = self.pickEligible() orelse return Error.NoEligibleNode;
        return self.dispatchTo(n, job);
    }

    fn dispatchTo(self: *Mesh, n: *Node, job: protocol.Job) Error!JobOutcome {
        self.stats.dispatched += 1;

        const receipt = n.execute(job) catch {
            self.stats.unreachable_jobs += 1;
            return .{
                .node_id = n.id,
                .node_name = n.name,
                .physical = n.isPhysical(),
                .y = 0,
                .verdict = .bad_status,
                .settlement = .{ .node_id = n.id, .outcome = .not_eligible, .detail = "node unreachable" },
                .unreachable_node = true,
            };
        };

        if (job.nonceValue() > n.highest_nonce_issued) n.highest_nonce_issued = job.nonceValue();

        if (!self.issuedByThisRun(job.nonceValue())) {
            self.stats.not_recorded_jobs += 1;
            return .{
                .node_id = n.id,
                .node_name = n.name,
                .physical = n.isPhysical(),
                .y = receipt.y,
                .verdict = protocol.verifyWithKey(job, receipt, n.key),
                .settlement = .{ .node_id = n.id, .outcome = .not_recorded, .detail = "nonce not issued by this coordinator run" },
            };
        }

        var verdict = protocol.verifyWithKey(job, receipt, n.key);

        // A nonce mismatch means either a replay attack or a stream that lost a
        // response and is now one behind. They are indistinguishable from a
        // single exchange, and one of them costs an honest operator their
        // stake — so use what the coordinator knows and the node does not: a
        // nonce we already issued is desync, a nonce we never issued is
        // fabrication.
        if (verdict == .nonce_mismatch) {
            const returned = std.mem.readInt(u32, &receipt.nonce, .little);
            if (returned <= n.highest_nonce_issued) verdict = .corrupt;
        }
        const settlement = try self.ledger.settle(n.id, job, receipt, verdict);

        // Exhaustive on purpose — no `else`. Three separate times this session a
        // new outcome fell into a catch-all and got printed as "rejected as
        // dishonest" beside "slashed: 0 mTRI", a summary that accuses and then
        // charges nothing. A reader cannot tell whether that is a lie or a bug.
        // With no `else`, adding an outcome is a compile error until someone
        // decides what it means.
        switch (settlement.outcome) {
            .credited => {
                self.stats.accepted += 1;
                n.stats.accepted += 1;
                if (n.isPhysical()) self.stats.on_silicon += 1 else self.stats.in_software += 1;
            },
            .corrupt_not_charged => self.stats.corrupt_jobs += 1,
            .unverifiable_not_charged => self.stats.unverifiable_jobs += 1,
            // We declined to use the node. That is our scheduling decision, not
            // the node's conduct.
            .not_eligible => self.stats.not_eligible_jobs += 1,
            .not_recorded => self.stats.not_recorded_jobs += 1,
            .rejected_and_slashed, .identity_mismatch => {
                self.stats.rejected += 1;
                n.stats.rejected += 1;
            },
        }

        return .{
            .node_id = n.id,
            .node_name = n.name,
            .physical = n.isPhysical(),
            .y = receipt.y,
            .verdict = verdict,
            .settlement = settlement,
        };
    }

    /// Send the same job to up to `k` distinct nodes and take the majority.
    ///
    /// For this work unit a quorum is not how correctness is established —
    /// recomputing a 32-wide dot product is cheaper than asking a second node.
    /// It is here because it is the mechanism that has to exist for work units
    /// where recomputation is NOT cheap, and because it is the honest way to
    /// measure whether independent nodes actually agree.
    pub fn dispatchQuorum(self: *Mesh, job: protocol.Job, k: usize) Error!QuorumOutcome {
        var votes: [16]i8 = undefined;
        var vote_count: usize = 0;
        const limit = @min(k, @min(self.nodes.items.len, votes.len));

        var tried: usize = 0;
        while (tried < self.nodes.items.len and vote_count < limit) : (tried += 1) {
            const idx = (self.cursor + tried) % self.nodes.items.len;
            const candidate = &self.nodes.items[idx];
            if (!self.ledger.isEligible(candidate.id)) continue;
            const out = try self.dispatchTo(candidate, job);
            if (out.unreachable_node) continue;
            votes[vote_count] = out.y;
            vote_count += 1;
        }
        self.cursor = (self.cursor + tried) % @max(self.nodes.items.len, 1);

        if (vote_count == 0) {
            return .{ .agreed = null, .responses = 0, .agreeing = 0, .majority_was_correct = false };
        }

        var best: i8 = votes[0];
        var best_n: u32 = 0;
        for (votes[0..vote_count]) |candidate| {
            var c: u32 = 0;
            for (votes[0..vote_count]) |v| {
                if (v == candidate) c += 1;
            }
            if (c > best_n) {
                best_n = c;
                best = candidate;
            }
        }

        const majority = best_n * 2 > @as(u32, @intCast(vote_count));
        const truth = protocol.dot(job.w, job.x);
        return .{
            .agreed = if (majority) best else null,
            .responses = @intCast(vote_count),
            .agreeing = best_n,
            .majority_was_correct = majority and best == truth,
        };
    }

    /// One ternary matrix-vector product, distributed across the mesh: each
    /// row of the weight matrix becomes one job. This is the operation a
    /// ternary-weight model's forward pass is made of, so a layer evaluated
    /// this way has its arithmetic physically spread over the network.
    pub fn matvec(
        self: *Mesh,
        rows: []const protocol.Packed,
        x: protocol.Packed,
        out: []i8,
        failures: ?*usize,
    ) Error!void {
        std.debug.assert(out.len >= rows.len);
        var fails: usize = 0;

        // Deal the rows out to eligible nodes first, then hand each node its
        // whole share in one go. One job per round trip costs a USB frame
        // interval, which held a layer to ~190 jobs/s while the same board
        // sustains 6842 batched — the work was already paid for and the
        // coordinator was not collecting it.
        //
        // Dispatch, judgement and settlement stay three separate steps:
        // executeBatch returns untrusted claims, every one of them is judged
        // against an independent recomputation, and only then is anything
        // credited.
        var eligible: [max_batch]usize = undefined;
        var n_eligible: usize = 0;
        for (self.nodes.items, 0..) |*n, idx| {
            if (n_eligible >= eligible.len) break;
            if (self.ledger.isEligible(n.id)) {
                eligible[n_eligible] = idx;
                n_eligible += 1;
            }
        }
        if (n_eligible == 0) return Error.NoEligibleNode;

        var jobs: [max_batch]protocol.Job = undefined;
        var receipts: [max_batch]protocol.Receipt = undefined;
        var row_of: [max_batch]usize = undefined;

        // Share the layer out rather than filling one node's batch before
        // starting the next. Dealing in fixed blocks of max_batch sent a
        // 32-row layer entirely to the first node, which is both unbalanced
        // and would have hidden a misbehaving node that never received work.
        const share = @max(1, @min(max_batch, (rows.len + n_eligible - 1) / n_eligible));

        var next_row: usize = 0;
        var turn: usize = 0;
        while (next_row < rows.len) : (turn += 1) {
            const node_idx = eligible[turn % n_eligible];
            const n = &self.nodes.items[node_idx];
            // Never hand a node more than it has been earning.
            const take = @min(@min(share, n.batch_limit), rows.len - next_row);

            for (0..take) |k| {
                const row = next_row + k;
                const nonce = try self.freshNonce();
                if (nonce > n.highest_nonce_issued) n.highest_nonce_issued = nonce;
                jobs[k] = protocol.Job.withNonce(nonce, rows[row], x);
                row_of[k] = row;
            }

            const got = n.executeBatch(jobs[0..take], receipts[0..take]) catch 0;
            self.stats.dispatched += take;

            for (0..take) |k| {
                const row = row_of[k];
                var credited = false;
                if (k < got) {
                    var verdict = protocol.verifyWithKey(jobs[k], receipts[k], n.key);
                    if (verdict == .nonce_mismatch) {
                        const returned = std.mem.readInt(u32, &receipts[k].nonce, .little);
                        if (returned <= n.highest_nonce_issued) verdict = .corrupt;
                    }
                    const settlement = try self.ledger.settle(n.id, jobs[k], receipts[k], verdict);
                    switch (settlement.outcome) {
                        .credited => {
                            out[row] = receipts[k].y;
                            credited = true;
                            self.stats.accepted += 1;
                            n.stats.accepted += 1;
                            if (n.isPhysical()) self.stats.on_silicon += 1 else self.stats.in_software += 1;
                        },
                        .corrupt_not_charged => self.stats.corrupt_jobs += 1,
                        .unverifiable_not_charged => self.stats.unverifiable_jobs += 1,
                        .not_eligible => self.stats.not_eligible_jobs += 1,
                        .not_recorded => self.stats.not_recorded_jobs += 1,
                        .rejected_and_slashed, .identity_mismatch => {
                            self.stats.rejected += 1;
                            n.stats.rejected += 1;
                        },
                    }
                } else {
                    // The batch came back short. Batching multiplies the cost
                    // of a marginal link — one lost byte and every later
                    // response in the run is gone — so the jobs the batch did
                    // not cover are retried one at a time rather than written
                    // off. An optimisation must not cost availability.
                    if (self.dispatchTo(n, jobs[k])) |single| {
                        if (single.settlement.outcome == .credited) {
                            out[row] = single.y;
                            credited = true;
                        }
                    } else |_| {}
                }
                if (!credited) {
                    // A row that was not credited is not silently accepted. The
                    // coordinator recomputes it so the layer still carries the
                    // right value, and the node still is not paid for it.
                    out[row] = protocol.dot(rows[row], x);
                    fails += 1;
                }
            }
            next_row += take;
        }

        if (failures) |f| f.* = fails;
    }

    pub fn report(self: *Mesh, writer: anytype) !void {
        try writer.print("nodes: {d} total, {d} physical\n", .{ self.nodeCount(), self.physicalCount() });
        for (self.nodes.items) |n| {
            const a = self.ledger.get(n.id) orelse continue;
            try writer.print(
                "  {s:<14} id={x:0>8} {s:<9} owner={s:<8} accepted={d:<6} rejected={d:<4} credit={d} mTRI stake={d} status={s}\n",
                .{ n.name, n.id, n.kindName(), a.owner, a.accepted, a.rejected, a.credit_mtri, a.stake_mtri, @tagName(a.status) },
            );
        }
        try writer.print(
            "jobs: {d} dispatched, {d} accepted, {d} rejected as dishonest, {d} damaged in transit, {d} unverifiable, {d} not dispatched, {d} unreachable\n",
            .{ self.stats.dispatched, self.stats.accepted, self.stats.rejected, self.stats.corrupt_jobs, self.stats.unverifiable_jobs, self.stats.not_eligible_jobs, self.stats.unreachable_jobs },
        );
        try writer.print(
            "dispatch: {d} to serial-attached nodes, {d} to software nodes ({d:.1}%)\n",
            .{ self.stats.on_silicon, self.stats.in_software, self.stats.siliconShare() * 100 },
        );
        try writer.print(
            "  (transport type, NOT evidence of where the arithmetic ran — see docs §5)\n",
            .{},
        );
        try writer.print(
            "credit issued: {d} mTRI, slashed: {d} mTRI\n",
            .{ self.ledger.total_credited_mtri, self.ledger.total_slashed_mtri },
        );
    }
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

fn testJob(n: u32) protocol.Job {
    var wv: protocol.Trits = @splat(0);
    var xv: protocol.Trits = @splat(0);
    for (&wv, 0..) |*t, i| t.* = if ((i + n) % 3 == 0) 1 else if ((i + n) % 3 == 1) -1 else 0;
    for (&xv, 0..) |*t, i| t.* = if ((i + n) % 2 == 0) 1 else -1;
    return protocol.Job.withNonce(n, protocol.pack(wv), protocol.pack(xv));
}

test "an all-honest mesh credits every job" {
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(1, "a", .honest), "alice", 2000);
    try m.join(Node.initEmulated(2, "b", .honest), "bob", 2000);
    try m.join(Node.initEmulated(3, "c", .honest), "carol", 2000);

    for (0..90) |i| {
        const o = try m.dispatch(testJob(@intCast(i)));
        try std.testing.expectEqual(ledger_mod.Outcome.credited, o.settlement.outcome);
    }
    try std.testing.expectEqual(@as(u64, 90), m.stats.accepted);
    // Round robin should spread work evenly across three nodes.
    for ([_]u32{ 1, 2, 3 }) |id| {
        try std.testing.expectEqual(@as(u64, 30), m.ledger.get(id).?.accepted);
    }
}

test "a free rider in the mesh is suspended and stops receiving work" {
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(1, "honest-a", .honest), "alice", 2000);
    try m.join(Node.initEmulated(2, "cheat", .lazy), "mallory", 2000);
    try m.join(Node.initEmulated(3, "honest-b", .honest), "carol", 2000);

    for (0..120) |i| _ = try m.dispatch(testJob(@intCast(i)));

    const cheat = m.ledger.get(2).?;
    try std.testing.expectEqual(ledger_mod.Status.suspended, cheat.status);
    try std.testing.expect(cheat.slashed_mtri > 0);
    try std.testing.expect(!m.ledger.isEligible(2));

    // The honest nodes absorbed the work the cheat stopped receiving.
    try std.testing.expect(m.ledger.get(1).?.accepted > 30);
    try std.testing.expect(m.ledger.get(3).?.accepted > 30);
}

test "the mesh reports how much work actually ran on silicon" {
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    // No physical node attached: the honest answer is zero percent hardware.
    try m.join(Node.initEmulated(1, "sim-a", .honest), "alice", 2000);
    try m.join(Node.initEmulated(2, "sim-b", .honest), "bob", 2000);
    for (0..20) |i| _ = try m.dispatch(testJob(@intCast(i)));
    try std.testing.expectEqual(@as(f64, 0.0), m.stats.siliconShare());
    try std.testing.expectEqual(@as(u64, 0), m.ledger.jobs_on_silicon);
}

test "a quorum outvotes a minority of liars but is not asked to beat a majority" {
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(1, "h1", .honest), "alice", 2000);
    try m.join(Node.initEmulated(2, "h2", .honest), "bob", 2000);
    try m.join(Node.initEmulated(3, "liar", .lazy), "mallory", 2000);

    const job = testJob(11);
    const q = try m.dispatchQuorum(job, 3);
    try std.testing.expectEqual(@as(u32, 3), q.responses);
    try std.testing.expectEqual(protocol.dot(job.w, job.x), q.agreed.?);
    try std.testing.expect(q.majority_was_correct);
}

test "a matrix-vector product distributed over the mesh matches local arithmetic" {
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(1, "a", .honest), "alice", 2000);
    try m.join(Node.initEmulated(2, "b", .honest), "bob", 2000);

    var rows: [16]protocol.Packed = undefined;
    var prng: std.Random.DefaultPrng = .init(0xBEEF);
    const rand = prng.random();
    for (&rows) |*r| {
        var tv: protocol.Trits = @splat(0);
        for (&tv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
        r.* = protocol.pack(tv);
    }
    var xv: protocol.Trits = @splat(0);
    for (&xv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
    const x = protocol.pack(xv);

    var out: [16]i8 = undefined;
    var fails: usize = 0;
    try m.matvec(&rows, x, &out, &fails);
    try std.testing.expectEqual(@as(usize, 0), fails);
    for (rows, out) |r, y| try std.testing.expectEqual(protocol.dot(r, x), y);
}

test "a layer still computes correctly when a node in the mesh is lying" {
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(1, "honest", .honest), "alice", 5000);
    try m.join(Node.initEmulated(2, "liar", .lazy), "mallory", 5000);

    var rows: [32]protocol.Packed = undefined;
    var prng: std.Random.DefaultPrng = .init(0xC0DE);
    const rand = prng.random();
    for (&rows) |*r| {
        var tv: protocol.Trits = @splat(0);
        for (&tv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
        r.* = protocol.pack(tv);
    }
    var xv: protocol.Trits = @splat(0);
    for (&xv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
    const x = protocol.pack(xv);

    var out: [32]i8 = undefined;
    var fails: usize = 0;
    try m.matvec(&rows, x, &out, &fails);

    // Every output is right even though a node lied, and the liar was caught.
    for (rows, out) |r, y| try std.testing.expectEqual(protocol.dot(r, x), y);
    try std.testing.expect(fails > 0);
    try std.testing.expect(m.ledger.get(2).?.slashed_mtri > 0);
    try std.testing.expectEqual(@as(u64, 0), m.ledger.get(2).?.credit_mtri);
}

test "a node on the other end of a socket earns credit like any other" {
    const net = @import("net.zig");

    // This is the path a developer on another machine takes, so it is worth
    // exercising for real rather than asserting the types line up. Binding
    // before the server thread starts removes the accept/connect race without
    // a sleep.
    var listener = try net.listen("127.0.0.1", 39702);

    const Server = struct {
        fn run(l: *net.Listener) void {
            var backing = Node.initEmulated(0xBEEF0001, "peer", .honest);
            while (true) {
                var conn = l.accept() catch return;
                defer conn.close();
                while (true) {
                    var raw: [protocol.request_len]u8 = undefined;
                    conn.readExact(&raw) catch break;
                    const job: protocol.Job = .{
                        .op = raw[2],
                        .nonce = raw[3..7].*,
                        .w = raw[7..15].*,
                        .x = raw[15..23].*,
                    };
                    const r = backing.execute(job) catch break;
                    conn.writeAll(&protocol.encodeResponse(r)) catch break;
                }
            }
        }
    };
    const th = try std.Thread.spawn(.{}, Server.run, .{&listener});

    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initRemote(0xBEEF0001, "over-tcp", "127.0.0.1", 39702), "remote-dev", 5000);

    for (0..25) |i| {
        const o = try m.dispatch(testJob(@intCast(i)));
        try std.testing.expectEqual(ledger_mod.Outcome.credited, o.settlement.outcome);
    }
    try std.testing.expectEqual(@as(u64, 25), m.ledger.get(0xBEEF0001).?.credit_mtri);

    listener.close();
    th.detach();
}

test "keyed nodes are verified with their own key, not each other's" {
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();

    const key_a: [16]u8 = @splat(0xA1);
    const key_b: [16]u8 = @splat(0xB2);

    // A fleet gives each node its own key so a leak is confined to one board.
    // The coordinator therefore has to verify each node against its own key,
    // and getting that wrong is silent: node B's honest receipt would look
    // forged under node A's key.
    try m.join(Node.initEmulated(0xA000, "keyed-a", .honest).withKey(key_a), "alice", 5000);
    try m.join(Node.initEmulated(0xB000, "keyed-b", .honest).withKey(key_b), "bob", 5000);
    try m.join(Node.initEmulated(0xC000, "unkeyed", .honest), "carol", 5000);

    for (0..60) |i| {
        const o = try m.dispatch(testJob(@intCast(i)));
        try std.testing.expectEqual(ledger_mod.Outcome.credited, o.settlement.outcome);
    }
    for ([_]u32{ 0xA000, 0xB000, 0xC000 }) |id| {
        try std.testing.expectEqual(@as(u64, 20), m.ledger.get(id).?.accepted);
    }

    // Cross-checking is what would go unnoticed: an honest keyed receipt fails
    // under the wrong key, exactly as a forgery would.
    const job = testJob(99);
    const honest = protocol.executeKeyed(job, 0xA000, key_a);
    try std.testing.expectEqual(protocol.Verdict.ok, protocol.verifyWithKey(job, honest, key_a));
    try std.testing.expectEqual(protocol.Verdict.corrupt, protocol.verifyWithKey(job, honest, key_b));
    // And a keyed receipt with no key at all must never be waved through —
    // but must not be held against the node either. Holding the wrong key is a
    // statement about the receipt; holding no key is a statement about us.
    const no_key = protocol.verifyWithKey(job, honest, null);
    try std.testing.expectEqual(protocol.Verdict.unverifiable, no_key);
    try std.testing.expect(!no_key.accepted());
    try std.testing.expect(!no_key.indictsTheNode());
}

test "a layer is shared across nodes, not dumped on the first one" {
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(1, "a", .honest), "alice", 9000);
    try m.join(Node.initEmulated(2, "b", .honest), "bob", 9000);
    try m.join(Node.initEmulated(3, "c", .honest), "carol", 9000);

    // Batching in fixed blocks sent a whole 32-row layer to the first node,
    // which is unbalanced and — worse — would hide a misbehaving node that
    // never received any work to misbehave on.
    var rows: [30]protocol.Packed = undefined;
    var prng: std.Random.DefaultPrng = .init(0x5A5A);
    const rand = prng.random();
    for (&rows) |*r| {
        var tv: protocol.Trits = @splat(0);
        for (&tv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
        r.* = protocol.pack(tv);
    }
    var xv: protocol.Trits = @splat(0);
    for (&xv) |*t| t.* = rand.intRangeAtMost(i8, -1, 1);
    const x = protocol.pack(xv);

    var out: [30]i8 = undefined;
    var fails: usize = 0;
    try m.matvec(&rows, x, &out, &fails);
    try std.testing.expectEqual(@as(usize, 0), fails);
    for (rows, out) |r, y| try std.testing.expectEqual(protocol.dot(r, x), y);

    // Every node did some of it, and no node did all of it.
    for ([_]u32{ 1, 2, 3 }) |id| {
        const acc = m.ledger.get(id).?.accepted;
        try std.testing.expect(acc > 0);
        try std.testing.expect(acc < rows.len);
    }
}

test "a mesh with no eligible node fails loudly instead of silently faking work" {
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try std.testing.expectError(Error.NoEligibleNode, m.dispatch(testJob(0)));
}

// ---------------------------------------------------------------------------
// The coordinator's journal across restarts (gHashTag/trinity-fpga#906,
// gHashTag/t27 specs/trinet/node-work-credit.t27).

fn journalText(path: [:0]const u8, buf: []u8) []const u8 {
    const f = std.c.fopen(path.ptr, "r") orelse return "";
    defer _ = std.c.fclose(f);
    return buf[0..std.c.fread(buf.ptr, 1, buf.len, f)];
}

fn journaledJob(m: *Mesh, seed: u8) !protocol.Job {
    return protocol.Job.withNonce(try m.freshNonce(), @splat(seed), @splat(0x55));
}

/// Lay down a journal by hand, to restart on top of one a running coordinator
/// cannot produce on purpose: a torn tail, or a line that does not parse.
fn writeJournalText(path: [:0]const u8, text: []const u8) !void {
    const f = std.c.fopen(path.ptr, "w") orelse return error.JournalWrite;
    const written = std.c.fwrite(text.ptr, 1, text.len, f);
    if (std.c.fclose(f) != 0 or written != text.len) return error.JournalWrite;
}

test "a restarted coordinator never pays the same (node, nonce) twice" {
    if (comptime !@import("builtin").link_libc) return error.SkipZigTest;
    const j: journal_mod.Journal = .{ .path = "/tmp/trinet-mesh-restart-906.jsonl" };
    _ = std.c.unlink(j.path.ptr);
    defer _ = std.c.unlink(j.path.ptr);

    var saved: protocol.Job = undefined;
    {
        var m = try Mesh.init(std.testing.allocator, .{});
        defer m.deinit();
        try m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "dmitrii-f-t27", 100000);
        const r = try m.openJournal(j, "dmitrii-f-t27");
        try std.testing.expectEqual(@as(u32, 0), r.mark);
        for (0..3) |i| {
            const job = try journaledJob(&m, @intCast(i));
            if (i == 0) saved = job;
            const o = try m.dispatch(job);
            try std.testing.expectEqual(ledger_mod.Outcome.credited, o.settlement.outcome);
        }
        try std.testing.expectEqual(@as(u32, 1), saved.nonceValue());
    }
    {
        // The restart: a new process, the same journal.
        var m = try Mesh.init(std.testing.allocator, .{});
        defer m.deinit();
        try m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "dmitrii-f-t27", 100000);
        const r = try m.openJournal(j, "dmitrii-f-t27");
        try std.testing.expectEqual(@as(u64, 3), r.credits);
        try std.testing.expectEqual(@as(u32, 1024), r.mark);

        // The job saved from the first run earns nothing now: its nonce lies
        // below where this run resumed.
        const replayed = try m.dispatch(saved);
        try std.testing.expectEqual(ledger_mod.Outcome.not_recorded, replayed.settlement.outcome);
        try std.testing.expectEqual(@as(u64, 0), replayed.settlement.credit_delta_mtri);

        // New work resumes at the mark, never at 1.
        const fresh = try journaledJob(&m, 9);
        try std.testing.expectEqual(@as(u32, 1024), fresh.nonceValue());
        const o = try m.dispatch(fresh);
        try std.testing.expectEqual(ledger_mod.Outcome.credited, o.settlement.outcome);
    }
    var buf: [4096]u8 = undefined;
    const text = journalText(j.path, &buf);
    try std.testing.expectEqual(@as(usize, 4), std.mem.count(u8, text, "\"mtri\":1}"));
    try std.testing.expectEqual(@as(usize, 1), std.mem.count(u8, text, "\"nonce\":1,"));
    try std.testing.expect(std.mem.indexOf(u8, text, "{\"mark\":2048}") != null);
}

test "a coordinator run by the node's owner writes self-reported credits" {
    if (comptime !@import("builtin").link_libc) return error.SkipZigTest;
    const j: journal_mod.Journal = .{ .path = "/tmp/trinet-mesh-level-906.jsonl" };
    for ([_][]const u8{ "dmitrii-f-t27", "", "gHashTag" }) |operator| {
        _ = std.c.unlink(j.path.ptr);
        var m = try Mesh.init(std.testing.allocator, .{});
        defer m.deinit();
        try m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "dmitrii-f-t27", 100000);
        _ = try m.openJournal(j, operator);
        _ = try m.dispatch(try journaledJob(&m, 1));
        var buf: [1024]u8 = undefined;
        const text = journalText(j.path, &buf);
        const want: []const u8 = if (std.mem.eql(u8, operator, "gHashTag")) "\"level\":\"verified\"" else "\"level\":\"self_reported\"";
        try std.testing.expect(std.mem.indexOf(u8, text, want) != null);
    }
    _ = std.c.unlink(j.path.ptr);
}

test "a forged answer is refused and never reaches the journal" {
    if (comptime !@import("builtin").link_libc) return error.SkipZigTest;
    const j: journal_mod.Journal = .{ .path = "/tmp/trinet-mesh-forged-906.jsonl" };
    _ = std.c.unlink(j.path.ptr);
    defer _ = std.c.unlink(j.path.ptr);
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(0x4E4F4432, "lazy", .lazy), "dmitrii-f-t27", 100000);
    _ = try m.openJournal(j, "dmitrii-f-t27");
    const o = try m.dispatch(protocol.Job.withNonce(try m.freshNonce(), @splat(0x55), @splat(0x55)));
    try std.testing.expect(o.settlement.outcome != .credited);
    var buf: [1024]u8 = undefined;
    try std.testing.expectEqual(@as(usize, 0), std.mem.count(u8, journalText(j.path, &buf), "\"mtri\""));
}

test "every credit line names the owner its own node was registered to" {
    // The handle in the line is what a payout is read off, so it has to be the
    // node's owner -- not the coordinator's operator, and not whichever owner
    // happened to be settled first.
    if (comptime !@import("builtin").link_libc) return error.SkipZigTest;
    const j: journal_mod.Journal = .{ .path = "/tmp/trinet-mesh-owner-906.jsonl" };
    _ = std.c.unlink(j.path.ptr);
    defer _ = std.c.unlink(j.path.ptr);

    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "developer-1", 100000);
    try m.join(Node.initEmulated(0x4E4F4432, "peer-2", .honest), "developer-2", 100000);
    _ = try m.openJournal(j, "gHashTag");

    // Round-robin: one job each.
    for (0..2) |_| {
        const o = try m.dispatch(try journaledJob(&m, 1));
        try std.testing.expectEqual(ledger_mod.Outcome.credited, o.settlement.outcome);
    }

    var buf: [4096]u8 = undefined;
    const text = journalText(j.path, &buf);
    try std.testing.expectEqual(@as(usize, 1), std.mem.count(u8, text, "\"owner\":\"developer-1\""));
    try std.testing.expectEqual(@as(usize, 1), std.mem.count(u8, text, "\"owner\":\"developer-2\""));
    try std.testing.expectEqual(@as(usize, 2), std.mem.count(u8, text, "\"operator\":\"gHashTag\""));
    // The operator is not an owner of anything here.
    try std.testing.expectEqual(@as(usize, 0), std.mem.count(u8, text, "\"owner\":\"gHashTag\""));
}

test "a journal torn mid-line refuses to open, twice over, and is left untouched" {
    // The regression this exists for: the coordinator used to open a torn
    // journal successfully and carry on writing. `append` uses fopen("a"), so
    // the first mark it wrote was glued onto the unfinished line -- see
    // journal.zig "writing on top of a torn tail is what corrupts a journal"
    // for what that does. One start cannot see it; the SECOND start is where
    // the damage surfaces, so this test performs both.
    if (comptime !@import("builtin").link_libc) return error.SkipZigTest;
    const j: journal_mod.Journal = .{ .path = "/tmp/trinet-mesh-torn-906.jsonl" };
    defer _ = std.c.unlink(j.path.ptr);
    // A mark, one whole credit, and a line the process died inside.
    const torn = "{\"mark\":1024}\n" ++
        "{\"t\":1,\"operator\":\"\",\"owner\":\"dmitrii-f-t27\",\"node\":\"4e4f4431\",\"physical\":false,\"nonce\":1,\"y\":3,\"tag\":\"0000000000000000\",\"tag_kind\":\"crc32\",\"level\":\"self_reported\",\"mtri\":1}\n" ++
        "{\"t\":2,\"operator\":\"\",\"owner\":\"dmitrii-f-t27\",\"node\":\"4e4f4431\",\"physi";
    try writeJournalText(j.path, torn);

    // First start after the crash: refused before any nonce or credit.
    {
        var m = try Mesh.init(std.testing.allocator, .{});
        defer m.deinit();
        try m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "dmitrii-f-t27", 100000);
        try std.testing.expectError(journal_mod.Error.JournalTornTail, m.openJournal(j, ""));
        try std.testing.expect(m.ledger.journal == null);
        try std.testing.expectEqual(@as(u32, 0), m.nonce_mark);
        try std.testing.expectEqual(@as(u64, 0), m.ledger.total_credited_mtri);
    }

    var buf: [4096]u8 = undefined;
    try std.testing.expectEqualStrings(torn, journalText(j.path, &buf));

    // Second start: the same named error, not a journal that has since become
    // unparsable, and still not a byte written.
    {
        var m = try Mesh.init(std.testing.allocator, .{});
        defer m.deinit();
        try m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "dmitrii-f-t27", 100000);
        try std.testing.expectError(journal_mod.Error.JournalTornTail, m.openJournal(j, ""));
    }
    var buf2: [4096]u8 = undefined;
    try std.testing.expectEqualStrings(torn, journalText(j.path, &buf2));

    // Read-only inspection still describes the file rather than refusing it:
    // one whole credit, the mark, and a tail somebody has to decide about.
    var spent: std.AutoHashMapUnmanaged(u64, void) = .empty;
    defer spent.deinit(std.testing.allocator);
    const seen = try j.restore(std.testing.allocator, &spent);
    try std.testing.expect(seen.torn_tail);
    try std.testing.expectEqual(@as(u64, 1), seen.credits);
    try std.testing.expectEqual(@as(u32, 1024), seen.mark);
}

test "a journal line that does not parse stops the coordinator, it does not read as empty" {
    if (comptime !@import("builtin").link_libc) return error.SkipZigTest;
    const j: journal_mod.Journal = .{ .path = "/tmp/trinet-mesh-malformed-906.jsonl" };
    defer _ = std.c.unlink(j.path.ptr);
    // A complete line with no node field: we cannot tell what it paid.
    try writeJournalText(j.path, "{\"mark\":1024}\n{\"t\":1,\"owner\":\"dmitrii-f-t27\",\"nonce\":1,\"mtri\":1}\n");

    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "dmitrii-f-t27", 100000);
    try std.testing.expectError(journal_mod.Error.JournalMalformed, m.openJournal(j, ""));
    // The journal was not adopted, so nothing here can credit against it. The
    // runner turns this error into a refusal to start (main.zig).
    try std.testing.expect(m.ledger.journal == null);
    try std.testing.expectEqual(@as(u64, 0), m.ledger.total_credited_mtri);
}

test "an unwritable journal issues no nonce and credits nothing" {
    if (comptime !@import("builtin").link_libc) return error.SkipZigTest;
    const j: journal_mod.Journal = .{ .path = "/nonexistent-dir-906/journal.jsonl" };
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "dmitrii-f-t27", 100000);
    _ = try m.openJournal(j, "dmitrii-f-t27");
    try std.testing.expectError(Error.JournalWrite, m.freshNonce());

    // Even a job whose nonce slipped through earns nothing it cannot record.
    m.nonce_mark = 10;
    m.next_nonce = 2;
    m.resumed_from = 1;
    const o = try m.dispatch(protocol.Job.withNonce(1, @splat(0x55), @splat(0x55)));
    try std.testing.expectEqual(ledger_mod.Outcome.not_recorded, o.settlement.outcome);
    try std.testing.expectEqual(@as(u64, 0), m.ledger.total_credited_mtri);
}

test "an unreadable journal refuses to open" {
    if (comptime !@import("builtin").link_libc) return error.SkipZigTest;
    var m = try Mesh.init(std.testing.allocator, .{});
    defer m.deinit();
    try std.testing.expectError(journal_mod.Error.JournalUnreadable, m.openJournal(.{ .path = "/tmp" }, ""));
}

test "an owner name the journal cannot hold is refused at the door, not at payout" {
    if (comptime !@import("builtin").link_libc) return error.SkipZigTest;
    const j: journal_mod.Journal = .{ .path = "/tmp/trinet-mesh-handle-906.jsonl" };
    _ = std.c.unlink(j.path.ptr);
    defer _ = std.c.unlink(j.path.ptr);
    {
        var m = try Mesh.init(std.testing.allocator, .{});
        defer m.deinit();
        _ = try m.openJournal(j, "");
        try std.testing.expectError(Error.InvalidOwner, m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "developer 1", 100000));
        try m.join(Node.initEmulated(0x4E4F4432, "peer-2", .honest), "developer-2", 100000);
    }
    {
        // Joined before the journal opened: the journal refuses to open.
        var m = try Mesh.init(std.testing.allocator, .{});
        defer m.deinit();
        try m.join(Node.initEmulated(0x4E4F4431, "peer-1", .honest), "a\"b", 100000);
        try std.testing.expectError(journal_mod.Error.InvalidHandle, m.openJournal(j, ""));
    }
    {
        var m = try Mesh.init(std.testing.allocator, .{});
        defer m.deinit();
        try std.testing.expectError(journal_mod.Error.InvalidHandle, m.openJournal(j, "op erator"));
    }
}
