// node --test .github/scripts/cache-prune.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { plan, run, seriesOf, prNumberOf, BUDGET_BYTES } = require('./cache-prune.js');

const ZIG = 'setup-zig-cache-v2-brain_unit-zig-x86_64-linux-0.16.0-';
let nextId = 1;
function entry(ref, key, created, size = 100, accessed = undefined) {
  const e = { id: nextId++, ref, key, created_at: created, size_in_bytes: size };
  const read = accessed !== undefined;
  if (read) e.last_accessed_at = accessed;
  return e;
}
const ids = (list) => list.map((x) => (x.entry || x).id).sort((a, b) => a - b);

test('series: a run id at the end makes a series, a fixed key does not', () => {
  assert.equal(seriesOf(`${ZIG}-37193965908-1`), ZIG);
  assert.equal(seriesOf(`${ZIG}-37193965908`), ZIG);
  assert.equal(seriesOf('setup-zig-tarball-zig-x86_64-linux-0.16.0'), null);
  assert.equal(seriesOf('chipdb-xc7a200tfbg484-2-0123456789abcdef0123456789abcdef-bbasm-le-v1'), null);
  // The part number ends in a short digit run; that is not a run id.
  assert.equal(seriesOf('chipdb-xc7a200tfbg484-2'), null);
});

test('pull request number comes only from a merge ref', () => {
  assert.equal(prNumberOf('refs/pull/839/merge'), 839);
  assert.equal(prNumberOf('refs/heads/main'), null);
  assert.equal(prNumberOf('refs/heads/pull/839/merge'), null);
});

test('every entry of a closed pull request is dropped, even the newest', () => {
  const a = entry('refs/pull/839/merge', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z');
  const b = entry('refs/pull/839/merge', 'setup-zig-tarball-zig-x86_64-linux-0.16.0', '2026-10-01T00:00:00Z');
  const { drop, keep } = plan([a, b], new Set([839]), new Set());
  assert.deepEqual(ids(drop), ids([a, b]));
  assert.equal(keep.length, 0);
});

test('an open pull request keeps the newest entry of each series', () => {
  const old = entry('refs/pull/852/merge', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z');
  const neu = entry('refs/pull/852/merge', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z');
  const { drop, keep } = plan([neu, old], new Set([839]), new Set());
  assert.deepEqual(ids(drop), ids([old]));
  assert.deepEqual(ids(keep), ids([neu]));
});

test('the same series on two refs is two series', () => {
  const m1 = entry('refs/heads/main', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z');
  const m2 = entry('refs/heads/main', `${ZIG}-37000000003-1`, '2026-10-03T00:00:00Z');
  const p1 = entry('refs/pull/852/merge', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z');
  const { drop, keep } = plan([m1, m2, p1], new Set(), new Set());
  assert.deepEqual(ids(drop), ids([m1]));
  assert.deepEqual(ids(keep), ids([m2, p1]));
});

test('fixed keys are kept on a live ref', () => {
  const t = entry('refs/heads/main', 'setup-zig-tarball-zig-x86_64-linux-0.16.0', '2026-09-07T00:00:00Z');
  const c = entry('refs/heads/main', 'chipdb-xc7a200tfbg484-2-0123abc-bbasm-le-v1', '2026-10-04T00:00:00Z');
  const { drop, keep } = plan([t, c], new Set(), new Set());
  assert.equal(drop.length, 0);
  assert.deepEqual(ids(keep), ids([t, c]));
});

test('a deleted branch loses its entries', () => {
  const g = entry('refs/heads/feat/gone', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z');
  const l = entry('refs/heads/feat/live', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z');
  const { drop, keep } = plan([g, l], new Set(), new Set(['refs/heads/feat/gone']));
  assert.deepEqual(ids(drop), ids([g]));
  assert.deepEqual(ids(keep), ids([l]));
});

test('equal creation times keep the higher id', () => {
  const a = entry('refs/heads/main', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z');
  const b = entry('refs/heads/main', `${ZIG}-37000000001-2`, '2026-10-01T00:00:00Z');
  const { keep } = plan([b, a], new Set(), new Set());
  assert.deepEqual(ids(keep), ids([b]));
});

test('plan accounts for every entry exactly once', () => {
  const all = [
    entry('refs/heads/main', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z'),
    entry('refs/heads/main', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z'),
    entry('refs/pull/1/merge', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z'),
    entry('refs/heads/main', 'setup-zig-tarball-x', '2026-10-02T00:00:00Z'),
  ];
  const { drop, keep } = plan(all, new Set([1]), new Set());
  assert.deepEqual([...ids(drop), ...ids(keep)].sort((a, b) => a - b), ids(all));
});

const CHIPDB = 'chipdb-xc7a200tfbg484-2-0123abc-bbasm-le-v1';

test('over the budget, per-run entries off main go, least recently read first', () => {
  const a = entry('refs/pull/852/merge', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z', 100, '2026-10-04T03:00:00Z');
  const b = entry('refs/heads/fix/x', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z', 100, '2026-10-04T01:00:00Z');
  const c = entry('refs/pull/853/merge', `${ZIG}-37000000003-1`, '2026-10-03T00:00:00Z', 100, '2026-10-04T02:00:00Z');
  const { drop, keep } = plan([a, b, c], new Set(), new Set(), 150);
  assert.deepEqual(ids(drop), ids([b, c]));
  assert.deepEqual(ids(keep), ids([a]));
  assert.match(drop[0].reason, /budget/);
});

test('the budget never drops a fixed key or an entry on main, even when still over', () => {
  const chip = entry('refs/pull/852/merge', CHIPDB, '2026-10-01T00:00:00Z', 70, '2026-10-01T00:00:00Z');
  const tar = entry('refs/heads/fix/x', 'setup-zig-tarball-x', '2026-10-01T00:00:00Z', 60, '2026-10-01T00:00:00Z');
  const main = entry('refs/heads/main', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z', 100, '2026-10-01T00:00:00Z');
  const pr = entry('refs/pull/852/merge', `${ZIG}-37000000002-1`, '2026-10-05T00:00:00Z', 100, '2026-10-05T00:00:00Z');
  const { drop, keep } = plan([chip, tar, main, pr], new Set(), new Set(), 10);
  assert.deepEqual(ids(drop), ids([pr]));
  assert.deepEqual(ids(keep), ids([chip, tar, main]));
});

// A chipdb keyed on its run id, as a branch saved on 2026-10-05.
test('the budget never drops a chipdb, even one whose key ends in a run id', () => {
  const chip = entry('refs/heads/fix/x', 'chipdb-xc7a200tfbg484-2-regymm-37246227964', '2026-10-01T00:00:00Z', 70, '2026-10-01T00:00:00Z');
  const zig = entry('refs/heads/fix/x', `${ZIG}-37000000002-1`, '2026-10-05T00:00:00Z', 100, '2026-10-05T00:00:00Z');
  assert.notEqual(seriesOf(chip.key), null);
  const { drop, keep } = plan([chip, zig], new Set(), new Set(), 10);
  assert.deepEqual(ids(drop), ids([zig]));
  assert.deepEqual(ids(keep), ids([chip]));
});

test('the budget never drops a setup-zig cache key that has no run id', () => {
  const fixed = entry('refs/heads/fix/x', `${ZIG}-pinned`, '2026-10-01T00:00:00Z', 70, '2026-10-01T00:00:00Z');
  const zig = entry('refs/heads/fix/x', `${ZIG}-37000000002-1`, '2026-10-05T00:00:00Z', 100, '2026-10-05T00:00:00Z');
  const { drop, keep } = plan([fixed, zig], new Set(), new Set(), 10);
  assert.deepEqual(ids(drop), ids([zig]));
  assert.deepEqual(ids(keep), ids([fixed]));
});

test('under the budget, the budget drops nothing', () => {
  const a = entry('refs/pull/852/merge', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z', 100);
  const b = entry('refs/heads/fix/x', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z', 100);
  const { drop } = plan([a, b], new Set(), new Set(), 200);
  assert.equal(drop.length, 0);
});

test('the last read decides, not the creation time', () => {
  const oldButRead = entry('refs/pull/1/merge', `${ZIG}-37000000001-1`, '2026-09-01T00:00:00Z', 100, '2026-10-05T00:00:00Z');
  const newButIdle = entry('refs/pull/2/merge', `${ZIG}-37000000002-1`, '2026-10-04T00:00:00Z', 100, '2026-10-04T00:00:00Z');
  const { drop } = plan([oldButRead, newButIdle], new Set(), new Set(), 100);
  assert.deepEqual(ids(drop), ids([newButIdle]));
});

test('with a budget, plan still accounts for every entry exactly once', () => {
  const all = [
    entry('refs/heads/main', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z'),
    entry('refs/heads/main', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z'),
    entry('refs/pull/1/merge', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z'),
    entry('refs/pull/2/merge', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z'),
    entry('refs/pull/2/merge', CHIPDB, '2026-10-02T00:00:00Z'),
  ];
  const { drop, keep } = plan(all, new Set([1]), new Set(), 150);
  assert.deepEqual([...ids(drop), ...ids(keep)].sort((a, b) => a - b), ids(all));
});

// A stand-in for the github-script client: enough of octokit for run().
function fakeGithub(caches, { closed = [], gone = [], deleteStatus = {} } = {}) {
  const deleted = [];
  const err = (status) => Object.assign(new Error(`HTTP ${status}`), { status });
  return {
    deleted,
    paginate: async () => caches,
    rest: {
      actions: {
        getActionsCacheList: () => {},
        deleteActionsCacheById: async ({ cache_id }) => {
          const status = deleteStatus[cache_id];
          if (status) throw err(status);
          deleted.push(cache_id);
        },
      },
      pulls: { get: async ({ pull_number }) => ({ data: { state: closed.includes(pull_number) ? 'closed' : 'open' } }) },
      repos: {
        getBranch: async ({ branch }) => {
          if (gone.includes(branch)) throw err(404);
          return { data: {} };
        },
      },
    },
  };
}

function fakeCore() {
  const core = { failed: null, info() {}, warning() {}, setFailed(m) { core.failed = m; } };
  const summary = { addHeading: () => summary, addTable: () => summary, write: async () => summary };
  core.summary = summary;
  return core;
}

const context = { repo: { owner: 'o', repo: 'r' } };

function fixture() {
  return [
    entry('refs/pull/839/merge', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z'),
    entry('refs/heads/main', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z'),
    entry('refs/heads/main', `${ZIG}-37000000002-1`, '2026-10-02T00:00:00Z'),
    entry('refs/heads/feat/gone', 'setup-zig-tarball-x', '2026-10-02T00:00:00Z'),
    entry('refs/heads/main', 'setup-zig-tarball-x', '2026-10-02T00:00:00Z'),
  ];
}

test('a dry run deletes nothing', async () => {
  const caches = fixture();
  const github = fakeGithub(caches, { closed: [839], gone: ['feat/gone'] });
  const core = fakeCore();
  await run({ github, context, core, dryRun: true });
  assert.deepEqual(github.deleted, []);
  assert.equal(core.failed, null);
});

test('a real run deletes exactly what plan drops', async () => {
  const caches = fixture();
  const github = fakeGithub(caches, { closed: [839], gone: ['feat/gone'] });
  const core = fakeCore();
  await run({ github, context, core, dryRun: false });
  const [pr, mainOld, , gone] = caches;
  assert.deepEqual(github.deleted.sort((a, b) => a - b), ids([pr, mainOld, gone]));
  assert.equal(core.failed, null);
});

test('run passes its budget to plan, and defaults to BUDGET_BYTES', async () => {
  const open = (size) => [
    entry('refs/pull/852/merge', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z', size),
    entry('refs/heads/main', `${ZIG}-37000000001-1`, '2026-10-01T00:00:00Z', size),
  ];
  const tight = open(100);
  const g1 = fakeGithub(tight);
  await run({ github: g1, context, core: fakeCore(), dryRun: false, budget: 0 });
  assert.deepEqual(g1.deleted, ids([tight[0]]));
  const g2 = fakeGithub(open(100));
  await run({ github: g2, context, core: fakeCore(), dryRun: false });
  assert.deepEqual(g2.deleted, []);
  // 2 x 5 GB is over the default 8 GB, so the default budget drops the pull request's entry.
  const big = open(5e9);
  const g3 = fakeGithub(big);
  await run({ github: g3, context, core: fakeCore(), dryRun: false });
  assert.deepEqual(g3.deleted, ids([big[0]]));
  assert.equal(BUDGET_BYTES, 8e9);
});

test('an entry already deleted by someone else is not a failure', async () => {
  const caches = fixture();
  const github = fakeGithub(caches, { closed: [839], deleteStatus: { [caches[0].id]: 404 } });
  const core = fakeCore();
  await run({ github, context, core, dryRun: false });
  assert.equal(core.failed, null);
});

test('a refused delete fails the job', async () => {
  const caches = fixture();
  const github = fakeGithub(caches, { closed: [839], deleteStatus: { [caches[0].id]: 403 } });
  const core = fakeCore();
  await run({ github, context, core, dryRun: false });
  assert.match(core.failed, /1 cache entries could not be deleted/);
});

test('a branch lookup error other than 404 stops the run before any delete', async () => {
  const caches = fixture();
  const github = fakeGithub(caches, { closed: [839] });
  github.rest.repos.getBranch = async () => { throw Object.assign(new Error('HTTP 500'), { status: 500 }); };
  await assert.rejects(run({ github, context, core: fakeCore(), dryRun: false }), /HTTP 500/);
  assert.deepEqual(github.deleted, []);
});
