# tri brain hippocampus — Memory Consolidation

## Module
tri.brain.hippocampus

## Description
Hippocampus — episodic memory consolidation. Stores task→result mappings with similarity-based retrieval.

## Subcommands
- `tri brain hippocampus store <task_id> <result>` — Store episode
- `tri brain hippocampus retrieve <task_id>` — Retrieve by exact match
- `tri brain hippocampus recall <query>` — Recall by similarity
- `tri brain hippocampus consolidate` — Merge similar episodes
- `tri brain hippocampus forget <age_days>` — Delete old episodes

## Neuroanatomy
Hippocampus — memory formation, consolidation, spatial navigation.

## Structs
### Episode
- task_id: []const u8
- result: []const u8 (JSON-encoded)
- embedding: [128]f32 (VSA embedding for similarity)
- created_at: i64
- access_count: u32
- last_accessed: i64

### SimilarityResult
- episode: Episode
- similarity: f32 (cosine similarity, 0-1)

### ConsolidationConfig
- similarity_threshold: f32 (default: 0.85)
- min_episodes: u32 (default: 3)
- max_age_days: u32 (default: 30)

## Functions
### store(task_id, result, embedding) !void
Store new episode. Generates embedding if not provided.

### retrieve(task_id) ?Episode
Get episode by exact task_id match.

### recall(query_embedding, limit) []SimilarityResult
Find most similar episodes by embedding similarity.

### consolidate(config) !u32
Merge similar episodes (group by similarity_threshold).
Returns number of merges performed.

### forget(max_age_days) !u32
Delete episodes older than max_age_days.
Returns number of episodes deleted.

### updateAccess(episode) void
Update access_count and last_accessed timestamp.

## Constants
EMBEDDING_DIM: 128 (VSA dimension)
DEFAULT_SIMILARITY_THRESHOLD: 0.85
FORGETTING_CURVE_DAYS: 30 (episodes older than 30 days are candidates)
MIN_ACCESS_COUNT: 3 (episodes accessed < 3 times are forgotten first)

## Storage
- Path: `.trinity/hippocampus/episodes.json`
- Format: JSON array of Episode objects
- Index: `.trinity/hippocampus/index.json` (task_id → offset)

## Thread Safety
- RwLock protects episode storage
- Reads are concurrent (multiple recalls)
- Writes (store, consolidate, forget) are exclusive

## MNL Integration
After successful task: hippocampus.store(task_id, result, embedding)
After failed task: hippocampus.updateAccess(episode) (even failure is data)
Before retry: hippocampus.recall(query) → find similar past attempts

## Forgetting Curve
Episodes are forgotten based on:
1. Age (older = more likely to forget)
2. Access count (frequently accessed = stronger memory)
3. Time since last access (recent = stronger)

Formula: memory_strength = log(access_count + 1) / (age_days + 1)
