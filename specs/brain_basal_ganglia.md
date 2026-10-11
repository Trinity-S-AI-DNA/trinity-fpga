# tri brain basal-ganglia — Action Selection

## Module
tri.brain.basal_ganglia

## Description
Corpus striatum — action selection registry. Sharded hashmap for lock-free reads and minimal contention writes.

## Subcommands
- `tri brain basal-ganglia claim <task_id> <agent_id>` — Claim task
- `tri brain basal-ganglia release <task_id>` — Release task
- `tri brain basal-ganglia heartbeat <task_id>` — Update heartbeat
- `tri brain basal-ganglia status` — Show registry status

## Neuroanatomy
Basal Ganglia — action selection, habit formation, reward-based learning.

## Structs
### ClaimStatus
enum { active = 0, completed = 1, abandoned = 2 }

### TaskClaim
- task_id: []const u8
- agent_id: []const u8
- claimed_at: i64 (milliseconds)
- ttl_ms: u64 (time-to-live)
- status: ClaimStatus
- completed_at: ?i64
- last_heartbeat: i64

### Shard
- claims: HashMap(task_id, TaskClaim)
- lock: RwLock
- stats: ShardStats

### Registry
- shards: [16]Shard (power of 2 for fast hash)
- stats: RegistryStats

## Functions
### claim(task_id, agent_id, ttl_ms) !bool
Claim task for agent. Returns false if already claimed.

### release(task_id, agent_id) !void
Release task claim (completion or abandonment).

### heartbeat(task_id) !bool
Update task heartbeat. Returns false if claim expired.

### getStatus(task_id) ?TaskClaim
Get current claim status for task.

### isValid(claim) bool
Check if claim is still valid (active + not expired + heartbeat recent).

## Constants
SHARD_COUNT: 16 (power of 2)
HEARTBEAT_TIMEOUT_MS: 30000 (30 seconds)
DEFAULT_TTL_MS: 300000 (5 minutes)

## Thread Safety
- All operations are thread-safe
- Atomic counters for statistics (lock-free reads)
- RwLock per shard allows concurrent reads
- Global mutex only for singleton initialization

## Performance
- With 16 shards: ~16x reduction in contention
- Single-threaded: ~5k OP/s (baseline)
- Multi-threaded: ~50k+ OP/s (theoretical with 16 threads)

## MNL Integration
Before claiming: check amygdala.isBlacklisted(task_id)
After abandonment: amygdala.recordFailure(task_id, reason)
