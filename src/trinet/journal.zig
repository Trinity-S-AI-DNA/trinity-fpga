//! The coordinator's durable journal: what it has paid, and how far it has
//! handed out nonces, so that neither is forgotten when the process stops.
//!
//! Spec: gHashTag/t27 specs/trinet/node-work-credit.t27 (gHashTag/t27#8426).
//! Issue: gHashTag/trinity-fpga#906.
//!
//! Before this file the paid (node, nonce) set lived in Ledger memory and the
//! coordinator's next nonce started at 1 on every start, so a restart paid the
//! same (node, nonce) again. The journal fixes both:
//!
//!   - a mark line `{"mark":N}` is written BEFORE any nonce at or above the
//!     previous mark is issued, so after a restart every nonce that may have
//!     gone out lies below the last mark and the coordinator resumes at it;
//!   - a credit line is written and flushed to disk BEFORE the credit counts,
//!     and the paid set is rebuilt from those lines on start.
//!
//! Reading fails closed. A missing file is a fresh network; a file that exists
//! but cannot be read, or holds a complete line we cannot parse, is an error:
//! "we do not know what was paid" must never read as "nothing was paid". Only a
//! torn LAST line (no newline: the process died mid-write) is skipped, because
//! a credit counts only after its whole line is on disk and a nonce is issued
//! only after its mark is.
//!
//! Skipped for READING is not the same as fit to WRITE. `append` opens with
//! fopen("a"), so the next line a run writes is glued onto the torn one, and
//! the result is worse than the tear: depending on where the process died the
//! glued line either stops parsing (so the journal becomes unreadable and
//! crediting stops for good) or parses as a credit and SWALLOWS the mark that
//! was glued into it (so the next start resumes at an older mark and reissues
//! nonces the previous run may already have handed out). A writing run
//! therefore refuses a torn journal at open -- `Ledger.openJournal` returns
//! `JournalTornTail` before any nonce or credit -- and leaves the file exactly
//! as it found it. Repairing it by truncation is NOT done here: another writer
//! may hold the same path, and an unproven repair of a payment record is worse
//! than a stop. `parse`/`restore` keep reporting `torn_tail` so a person or a
//! read-only tool can still inspect the file.
//!
//! The file is JSON lines so a person can read it and `jq` can sum it. It
//! needs libc for fsync; without libc a journal cannot be opened.

const std = @import("std");
const builtin = @import("builtin");

/// NONCE_RESERVE_BLOCK in node-work-credit.t27.
pub const reserve_block: u32 = 1024;

pub const Error = error{
    JournalUnavailable,
    JournalUnreadable,
    JournalMalformed,
    /// The last line is incomplete: a run died mid-write. Readable, but not
    /// writable -- see the note at the top of this file. The run stops with
    /// the file untouched instead of appending onto the tear.
    JournalTornTail,
    JournalWrite,
    NonceSpaceExhausted,
    /// An owner or operator name the journal cannot hold as written.
    InvalidHandle,
    OutOfMemory,
};

/// Who vouches for a credit (record levels in node-work-credit.t27). A credit
/// written by a coordinator whose operator is the node's owner, or whose
/// operator is unknown, is the owner's own word about their node.
pub const Level = enum {
    self_reported,
    verified,

    pub fn of(operator: []const u8, owner: []const u8) Level {
        if (operator.len == 0 or std.mem.eql(u8, operator, owner)) return .self_reported;
        return .verified;
    }
};

/// The spec's next_mark: 0 when the 32-bit nonce space is spent, never a wrap.
pub fn nextMark(mark: u32) u32 {
    if (mark > std.math.maxInt(u32) - reserve_block) return 0;
    return mark + reserve_block;
}

pub fn spentKey(node_id: u32, nonce: u32) u64 {
    return (@as(u64, node_id) << 32) | nonce;
}

pub const Restored = struct {
    /// The highest mark on disk; 0 for a fresh journal.
    mark: u32 = 0,
    credits: u64 = 0,
    /// The file's last line has no newline, so it was never finished. Reading
    /// skips it; a run that intends to WRITE must stop (`JournalTornTail`),
    /// because appending glues the next line onto this one.
    torn_tail: bool = false,
};

const C = struct {
    extern "c" fn fsync(fd: c_int) c_int;
    extern "c" fn fileno(f: *std.c.FILE) c_int;
    extern "c" fn access(path: [*:0]const u8, mode: c_int) c_int;
    extern "c" fn fflush(f: *std.c.FILE) c_int;
    extern "c" fn ferror(f: *std.c.FILE) c_int;
};

pub const Journal = struct {
    path: [:0]const u8,

    /// Append one line and put it on disk before returning.
    fn append(self: Journal, line: []const u8) Error!void {
        if (comptime !builtin.link_libc) return Error.JournalUnavailable;
        const f = std.c.fopen(self.path.ptr, "a") orelse return Error.JournalWrite;
        var ok = std.c.fwrite(line.ptr, 1, line.len, f) == line.len;
        ok = ok and C.fflush(f) == 0;
        ok = ok and C.fsync(C.fileno(f)) == 0;
        ok = std.c.fclose(f) == 0 and ok;
        if (!ok) return Error.JournalWrite;
    }

    pub fn writeMark(self: Journal, mark: u32) Error!void {
        var buf: [32]u8 = undefined;
        const line = std.fmt.bufPrint(&buf, "{{\"mark\":{d}}}\n", .{mark}) catch unreachable;
        return self.append(line);
    }

    pub const Credit = struct {
        operator: []const u8,
        owner: []const u8,
        node_id: u32,
        physical: bool,
        nonce: u32,
        y: i32,
        tag: u64,
        tag_kind: []const u8,
        level: Level,
        mtri: u64,
    };

    pub fn writeCredit(self: Journal, c: Credit) Error!void {
        // Handles go into the line unescaped, so only handle characters pass.
        if (!isHandle(c.owner) or !(c.operator.len == 0 or isHandle(c.operator))) return Error.JournalWrite;
        var ts: std.c.timespec = undefined;
        _ = std.c.clock_gettime(.REALTIME, &ts);
        var buf: [512]u8 = undefined;
        const line = std.fmt.bufPrint(&buf, "{{\"t\":{d},\"operator\":\"{s}\",\"owner\":\"{s}\",\"node\":\"{x:0>8}\",\"physical\":{},\"nonce\":{d},\"y\":{d},\"tag\":\"{x:0>16}\",\"tag_kind\":\"{s}\",\"level\":\"{s}\",\"mtri\":{d}}}\n", .{
            ts.sec, c.operator, c.owner, c.node_id, c.physical, c.nonce, c.y, c.tag, c.tag_kind, @tagName(c.level), c.mtri,
        }) catch return Error.JournalWrite;
        return self.append(line);
    }

    /// Rebuild the paid set and the nonce mark from disk.
    pub fn restore(self: Journal, gpa: std.mem.Allocator, spent: *std.AutoHashMapUnmanaged(u64, void)) Error!Restored {
        if (comptime !builtin.link_libc) return Error.JournalUnavailable;
        if (C.access(self.path.ptr, 0) != 0) return .{};
        const f = std.c.fopen(self.path.ptr, "r") orelse return Error.JournalUnreadable;
        defer _ = std.c.fclose(f);

        var text: std.ArrayList(u8) = .empty;
        defer text.deinit(gpa);
        var chunk: [4096]u8 = undefined;
        while (true) {
            const n = std.c.fread(&chunk, 1, chunk.len, f);
            if (n == 0) break;
            try text.appendSlice(gpa, chunk[0..n]);
        }
        if (C.ferror(f) != 0) return Error.JournalUnreadable;
        return parse(gpa, text.items, spent);
    }
};

/// A GitHub-style handle: letters, digits, `-` and `_`. Names go into the
/// journal unescaped, so only these pass, and the ledger refuses anything else
/// at registration rather than at payout.
pub fn isHandle(s: []const u8) bool {
    if (s.len == 0) return false;
    for (s) |ch| if (!std.ascii.isAlphanumeric(ch) and ch != '-' and ch != '_') return false;
    return true;
}

/// The text of a journal into the paid set and the mark. Public for tests.
pub fn parse(gpa: std.mem.Allocator, text: []const u8, spent: *std.AutoHashMapUnmanaged(u64, void)) Error!Restored {
    var out: Restored = .{};
    var rest = text;
    while (rest.len > 0) {
        const nl = std.mem.indexOfScalar(u8, rest, '\n') orelse {
            out.torn_tail = true;
            break;
        };
        const line = rest[0..nl];
        rest = rest[nl + 1 ..];
        if (line.len == 0) continue;

        if (std.mem.startsWith(u8, line, "{\"mark\":")) {
            const v = numberAfter(line, "{\"mark\":") orelse return Error.JournalMalformed;
            const m = std.fmt.parseInt(u32, v, 10) catch return Error.JournalMalformed;
            if (m > out.mark) out.mark = m;
            continue;
        }
        const node_hex = stringAfter(line, "\"node\":\"") orelse return Error.JournalMalformed;
        const node_id = std.fmt.parseInt(u32, node_hex, 16) catch return Error.JournalMalformed;
        const nonce_s = numberAfter(line, "\"nonce\":") orelse return Error.JournalMalformed;
        const nonce = std.fmt.parseInt(u32, nonce_s, 10) catch return Error.JournalMalformed;
        try spent.put(gpa, spentKey(node_id, nonce), {});
        out.credits += 1;
    }
    return out;
}

fn stringAfter(line: []const u8, key: []const u8) ?[]const u8 {
    const at = std.mem.indexOf(u8, line, key) orelse return null;
    const from = at + key.len;
    const end = std.mem.indexOfScalarPos(u8, line, from, '"') orelse return null;
    return line[from..end];
}

fn numberAfter(line: []const u8, key: []const u8) ?[]const u8 {
    const at = std.mem.indexOf(u8, line, key) orelse return null;
    const from = at + key.len;
    var end = from;
    while (end < line.len and std.ascii.isDigit(line[end])) end += 1;
    if (end == from) return null;
    return line[from..end];
}

// ---------------------------------------------------------------------------

const testing = std.testing;

/// Lay down a journal by hand, to read back a state a running coordinator
/// cannot produce on purpose.
fn writeText(path: [:0]const u8, text: []const u8) !void {
    const f = std.c.fopen(path.ptr, "w") orelse return error.JournalWrite;
    const n = std.c.fwrite(text.ptr, 1, text.len, f);
    if (std.c.fclose(f) != 0 or n != text.len) return error.JournalWrite;
}

test "the nonce mark moves in blocks and never wraps" {
    try testing.expectEqual(@as(u32, 1024), reserve_block);
    try testing.expectEqual(@as(u32, 1025), nextMark(1));
    try testing.expectEqual(@as(u32, 3072), nextMark(2048));
    try testing.expectEqual(@as(u32, std.math.maxInt(u32)), nextMark(std.math.maxInt(u32) - 1024));
    try testing.expectEqual(@as(u32, 0), nextMark(std.math.maxInt(u32) - 1023));
}

test "a record the node's owner vouches for is self-reported" {
    try testing.expectEqual(Level.self_reported, Level.of("", "dmitrii-f-t27"));
    try testing.expectEqual(Level.self_reported, Level.of("dmitrii-f-t27", "dmitrii-f-t27"));
    try testing.expectEqual(Level.verified, Level.of("gHashTag", "dmitrii-f-t27"));
}

test "a journal rebuilds the paid set and the highest mark" {
    var spent: std.AutoHashMapUnmanaged(u64, void) = .empty;
    defer spent.deinit(testing.allocator);
    const text =
        "{\"mark\":1024}\n" ++
        "{\"t\":1,\"operator\":\"\",\"owner\":\"a\",\"node\":\"4e4f4431\",\"physical\":false,\"nonce\":1,\"y\":3,\"tag\":\"00\",\"tag_kind\":\"keyed\",\"level\":\"self_reported\",\"mtri\":1}\n" ++
        "{\"mark\":2048}\n";
    const r = try parse(testing.allocator, text, &spent);
    try testing.expectEqual(@as(u32, 2048), r.mark);
    try testing.expectEqual(@as(u64, 1), r.credits);
    try testing.expect(spent.contains(spentKey(0x4E4F4431, 1)));
    try testing.expect(!r.torn_tail);
}

test "a torn last line is skipped, a malformed whole line is refused" {
    var spent: std.AutoHashMapUnmanaged(u64, void) = .empty;
    defer spent.deinit(testing.allocator);
    const torn = "{\"mark\":1024}\n{\"t\":1,\"owner\":\"a\",\"node\":\"4e4f";
    const r = try parse(testing.allocator, torn, &spent);
    try testing.expect(r.torn_tail);
    try testing.expectEqual(@as(u32, 1024), r.mark);
    try testing.expectEqual(@as(u64, 0), r.credits);

    try testing.expectError(Error.JournalMalformed, parse(testing.allocator, "{\"owner\":\"a\"}\n", &spent));
    try testing.expectError(Error.JournalMalformed, parse(testing.allocator, "{\"mark\":x}\n", &spent));
    try testing.expectError(Error.JournalMalformed, parse(testing.allocator, "{\"node\":\"zz\",\"nonce\":1}\n", &spent));
}

test "a missing journal is fresh, an unreadable one is an error" {
    if (comptime !builtin.link_libc) return error.SkipZigTest;
    var spent: std.AutoHashMapUnmanaged(u64, void) = .empty;
    defer spent.deinit(testing.allocator);
    const missing: Journal = .{ .path = "/tmp/trinet-journal-test-missing-906.jsonl" };
    _ = std.c.unlink(missing.path.ptr);
    const r = try missing.restore(testing.allocator, &spent);
    try testing.expectEqual(@as(u32, 0), r.mark);

    // A directory exists but cannot be read as a journal.
    const dir: Journal = .{ .path = "/tmp" };
    try testing.expectError(Error.JournalUnreadable, dir.restore(testing.allocator, &spent));
}

test "writing on top of a torn tail is what corrupts a journal" {
    if (comptime !builtin.link_libc) return error.SkipZigTest;
    // This is why a torn tail refuses to open for writing instead of being
    // appended to: `append` uses fopen("a"), so the next line the coordinator
    // writes is glued onto the unfinished one. Both halves below write exactly
    // the mark a resuming run would have written next, and neither result is
    // survivable.
    const head = "{\"mark\":1024}\n";

    // Torn before the nonce: the glued line stops parsing, so from here on the
    // journal is unreadable and crediting stops for good.
    {
        const j: Journal = .{ .path = "/tmp/trinet-journal-test-glue-early-906.jsonl" };
        _ = std.c.unlink(j.path.ptr);
        defer _ = std.c.unlink(j.path.ptr);
        try writeText(j.path, head ++ "{\"t\":2,\"operator\":\"\",\"owner\":\"a\",\"node\":\"4e4f4431\",\"physi");
        try j.writeMark(2048);

        var spent: std.AutoHashMapUnmanaged(u64, void) = .empty;
        defer spent.deinit(testing.allocator);
        try testing.expectError(Error.JournalMalformed, j.restore(testing.allocator, &spent));
    }

    // Torn a few fields later: worse, because it is silent. The glued line
    // parses as a paid credit and swallows the mark written into it, so the
    // next start resumes at the OLD mark and reissues nonces the previous run
    // may already have handed out -- the exact double payment this file exists
    // to prevent.
    {
        const j: Journal = .{ .path = "/tmp/trinet-journal-test-glue-late-906.jsonl" };
        _ = std.c.unlink(j.path.ptr);
        defer _ = std.c.unlink(j.path.ptr);
        try writeText(j.path, head ++ "{\"t\":2,\"operator\":\"\",\"owner\":\"a\",\"node\":\"4e4f4432\",\"physical\":false,\"nonce\":5,\"y\":3,\"tag\":\"00");
        try j.writeMark(2048);

        var spent: std.AutoHashMapUnmanaged(u64, void) = .empty;
        defer spent.deinit(testing.allocator);
        const r = try j.restore(testing.allocator, &spent);
        try testing.expectEqual(@as(u32, 1024), r.mark); // the 2048 just written is gone
        try testing.expect(spent.contains(spentKey(0x4E4F4432, 5)));
        try testing.expect(!r.torn_tail); // and it no longer looks damaged
    }
}

test "a mark and a credit written are read back" {
    if (comptime !builtin.link_libc) return error.SkipZigTest;
    const j: Journal = .{ .path = "/tmp/trinet-journal-test-roundtrip-906.jsonl" };
    _ = std.c.unlink(j.path.ptr);
    defer _ = std.c.unlink(j.path.ptr);
    try j.writeMark(1024);
    try j.writeCredit(.{ .operator = "", .owner = "dmitrii-f-t27", .node_id = 0x5452494E, .physical = true, .nonce = 7, .y = -2, .tag = 0xABCD, .tag_kind = "keyed", .level = .self_reported, .mtri = 1 });
    try testing.expectError(Error.JournalWrite, j.writeCredit(.{ .operator = "", .owner = "a\"b", .node_id = 1, .physical = false, .nonce = 1, .y = 0, .tag = 0, .tag_kind = "keyed", .level = .self_reported, .mtri = 1 }));

    var spent: std.AutoHashMapUnmanaged(u64, void) = .empty;
    defer spent.deinit(testing.allocator);
    const r = try j.restore(testing.allocator, &spent);
    try testing.expectEqual(@as(u32, 1024), r.mark);
    try testing.expectEqual(@as(u64, 1), r.credits);
    try testing.expect(spent.contains(spentKey(0x5452494E, 7)));
}
