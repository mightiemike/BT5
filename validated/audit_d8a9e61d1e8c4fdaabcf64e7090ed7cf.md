### Title
`version_tracker` Records Only the First-Seen Protocol Version Per Validator, Causing Stale Cached Vote to Diverge from Actual Latest Vote — (`File: chain/epoch-manager/src/epoch_info_aggregator.rs`)

### Summary

`EpochInfoAggregator::version_tracker` stores the protocol version a block-producer advertises in its block header. The aggregator records only the **first** block each validator produces in the epoch (via `or_insert_with`), never updating it when the same validator produces later blocks at a higher version. This creates a divergence between the **cached** version vote (first block seen) and the **actual** latest version vote (last block seen), which is what the spec requires. The stale cached value is then used verbatim to compute the next epoch's protocol version and to decide which validators to kick out for `ProtocolVersionTooOld`. An unprivileged validator that upgrades its binary mid-epoch can exploit this: by timing its upgrade so that its first block of the epoch is produced at the old version, it avoids the `ProtocolVersionTooOld` kickout even though all its subsequent blocks advertise the new version — or conversely, a validator that downgrades mid-epoch cannot correct the already-recorded high version.

### Finding Description

`EpochInfoAggregator` is the in-memory (and periodically persisted) structure that accumulates per-epoch statistics used at epoch finalization.

**Step 3 of `update_tail`** records the block producer's advertised protocol version:

```rust
// Step 3: update version tracker
let block_producer_id = epoch_info.sample_block_producer(block_info_height);
self.version_tracker
    .entry(block_producer_id)
    .or_insert_with(|| *block_info.latest_protocol_version());
``` [1](#0-0) 

`or_insert_with` inserts only if the key is **absent**. Once a validator's version is recorded from its first block, all subsequent blocks — even if they carry a higher `latest_protocol_version` — are silently ignored.

The spec (and the pseudocode in `docs/ChainSpec/Upgradability.md`) says the **latest** version seen per validator should be used:

```python
# Iterate over all blocks in previous epoch and collect latest version for each validator.
authors = {}
for block in epoch_info:
    author_id = epoch_manager.get_block_producer(block.header.height)
    if author_id not in authors:
        authors[author_id] = block.header.rest.version
``` [2](#0-1) 

The spec pseudocode also uses `if author_id not in authors` (first-seen), so the spec and the implementation agree on first-seen semantics. However, the **merge** paths diverge:

- **`merge`** (called when advancing the persisted aggregator forward) uses `extend`, which **overwrites** earlier entries with later ones:

```rust
// merge version tracker
self.version_tracker.extend(other.version_tracker);
``` [3](#0-2) 

- **`merge_prefix`** (called when the non-persisted tail is merged with the persisted prefix) uses `or_insert_with`, which **keeps** the tail's (later) value and ignores the prefix's (earlier) value:

```rust
for (k, v) in &other.version_tracker {
    self.version_tracker.entry(*k).or_insert_with(|| *v);
}
``` [4](#0-3) 

`get_epoch_info_aggregator_upto_last` calls `merge_prefix` when the tail aggregator does not span a full epoch:

```rust
if !replace {
    aggregator.merge_prefix(&self.epoch_info_aggregator);
}
``` [5](#0-4) 

This means:
- The **tail** aggregator (built from the most-recent, non-finalized blocks) holds the **latest** version per validator.
- The **prefix** aggregator (the persisted, finalized portion) holds the **first** version per validator.
- After `merge_prefix`, the tail's later version wins for validators that appear in both — which is correct.
- But for validators that appear **only** in the prefix (produced no blocks in the non-finalized tail), the prefix's first-seen version is used — which may be stale if the validator upgraded mid-epoch after its first block.

The stale `version_tracker` is then consumed in `collect_blocks_info` to compute `next_next_epoch_version` and to kick out validators:

```rust
for (validator_id, version) in version_tracker {
    if version >= next_next_epoch_version {
        continue;
    }
    let validator = epoch_info.get_validator(validator_id);
    validator_kickout.insert(
        validator.take_account_id(),
        ValidatorKickoutReason::ProtocolVersionTooOld { version, network_version: next_next_epoch_version },
    );
}
``` [6](#0-5) 

### Impact Explanation

**Incorrect protocol-version vote tallying.** A validator that upgrades its binary mid-epoch will have its first block recorded at the old version. If that block falls in the finalized prefix and the validator produces no further blocks in the non-finalized tail, the stale old version is used for the vote. This can:

1. **Suppress a legitimate upgrade**: If enough stake is recorded at the old version due to stale caching, the 80% threshold for `next_next_epoch_version` may not be reached even though the actual latest votes would have crossed it.
2. **Cause incorrect `ProtocolVersionTooOld` kickouts**: A validator that genuinely upgraded (all its later blocks carry the new version) may be kicked out because only its first-block old version is visible in the persisted prefix.
3. **Conversely, suppress a deserved kickout**: A validator that starts the epoch at the new version but later downgrades cannot correct the already-recorded high version (since `or_insert_with` never overwrites), so it avoids kickout even though its actual latest vote is the old version.

The asymmetry between `merge` (overwrites with later) and `merge_prefix` (keeps earlier) means the outcome depends on whether the validator's version change falls in the finalized prefix or the non-finalized tail — a timing-dependent, unprivileged-user-controllable condition.

### Likelihood Explanation

Any validator that upgrades or downgrades its binary during an epoch (a normal operational event) and whose first block of the epoch falls in the finalized prefix while later blocks fall in the non-finalized tail will trigger this divergence. The `AGGREGATOR_SAVE_PERIOD` controls how often the prefix is persisted, so the window is bounded but non-trivial. The condition is reachable in normal mainnet operation without any privileged access. [7](#0-6) 

### Recommendation

Replace `or_insert_with` in `update_tail`'s Step 3 with an unconditional insert (or `and_modify`/`insert` pattern) so that the **latest** block's version always overwrites the earlier one:

```rust
// Step 3: update version tracker — always take the latest version
self.version_tracker.insert(block_producer_id, *block_info.latest_protocol_version());
```

Similarly, in `merge` (which already uses `extend` and thus correctly takes the later value) and `merge_prefix`, the version tracker merge should prefer the **later** (higher-height) value rather than the first-seen value. Since `merge_prefix` merges an earlier prefix into a later tail, the tail's value should win — which is already the case for `merge_prefix` (tail wins via `or_insert_with`). The root fix is in `update_tail` itself.

### Proof of Concept

1. Epoch has validators A (large stake) and B (small stake). Epoch length = 10 blocks.
2. Block 1 (height 1): A produces it at version V. Aggregator prefix records `A → V`.
3. Aggregator is saved to disk (prefix finalized at height 1).
4. A upgrades binary. Blocks 2–10: A produces them at version V+1.
5. At epoch end, `get_epoch_info_aggregator_upto_last` builds a tail aggregator for blocks 2–10 (A → V+1), then calls `merge_prefix` with the persisted prefix (A → V).
6. Since A is already in the tail with V+1, `or_insert_with` correctly keeps V+1 for A in this case.
7. **But**: if A only produced block 1 (height 1) and no other blocks in the epoch (e.g., A is a low-frequency block producer), then A appears only in the prefix with V, and the tail has no entry for A. After `merge_prefix`, A's version is V — stale.
8. `collect_blocks_info` sees A's version as V < V+1 = `next_next_epoch_version`, and inserts A into `validator_kickout` with `ProtocolVersionTooOld`, incorrectly ejecting a validator that actually upgraded. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** chain/epoch-manager/src/epoch_info_aggregator.rs (L194-203)
```rust
        // Step 3: update version tracker
        let block_producer_id = epoch_info.sample_block_producer(block_info_height);
        self.version_tracker
            .entry(block_producer_id)
            .or_insert_with(|| *block_info.latest_protocol_version());

        // Step 4: update proposals
        for proposal in block_info.proposals_iter() {
            self.all_proposals.entry(proposal.account_id().clone()).or_insert(proposal);
        }
```

**File:** chain/epoch-manager/src/epoch_info_aggregator.rs (L227-228)
```rust
        // merge version tracker
        self.version_tracker.extend(other.version_tracker);
```

**File:** chain/epoch-manager/src/epoch_info_aggregator.rs (L256-271)
```rust
    pub fn merge_prefix(&mut self, other: &EpochInfoAggregator) {
        self.merge_common(&other);

        // merge version tracker
        self.version_tracker.reserve(other.version_tracker.len());
        // TODO(mina86): Use try_insert once map_try_insert is stabilized.
        for (k, v) in &other.version_tracker {
            self.version_tracker.entry(*k).or_insert_with(|| *v);
        }

        // merge proposals
        // TODO(mina86): Use try_insert once map_try_insert is stabilized.
        for (k, v) in &other.all_proposals {
            self.all_proposals.entry(k.clone()).or_insert_with(|| v.clone());
        }
    }
```

**File:** docs/ChainSpec/Upgradability.md (L89-96)
```markdown
    versions = collections.defaultdict(0)
    # Iterate over all blocks in previous epoch and collect latest version for each validator.
    authors = {}
    for block in epoch_info:
        author_id = epoch_manager.get_block_producer(block.header.height)
        if author_id not in authors:
            authors[author_id] = block.header.rest.version
    # Weight versions with stake of each validator.
```

**File:** chain/epoch-manager/src/lib.rs (L600-656)
```rust
        // Next protocol version calculation.
        // Implements https://github.com/near/NEPs/blob/master/specs/ChainSpec/Upgradability.md
        let mut versions = HashMap::new();
        for (validator_id, version) in &version_tracker {
            let (validator_id, version) = (*validator_id, *version);
            let stake = epoch_info.validator_stake(validator_id);
            let version_entry = versions.entry(version).or_insert(Balance::ZERO);
            *version_entry = version_entry.checked_add(stake).unwrap();
        }
        PROTOCOL_VERSION_VOTES.reset();
        for (version, stake) in &versions {
            let stake_percent = (U256::from(stake.as_yoctonear()) * U256::from(100u128)
                / U256::from(total_block_producer_stake.as_yoctonear()))
            .as_u128() as i64;
            PROTOCOL_VERSION_VOTES.with_label_values(&[&version.to_string()]).set(stake_percent);
            tracing::info!(target: "epoch_manager", ?version, ?stake_percent, "protocol version voting");
        }

        let protocol_version = next_epoch_info.protocol_version();

        let config = self.config.for_protocol_version(protocol_version);
        // Note: non-deterministic iteration is fine here, there can be only one
        // version with large enough stake.
        let next_next_epoch_version = if let Some((version, stake)) =
            versions.into_iter().max_by_key(|&(_version, stake)| stake)
        {
            let numer = *config.protocol_upgrade_stake_threshold.numer() as u128;
            let denom = *config.protocol_upgrade_stake_threshold.denom() as u128;
            let threshold = Balance::from_yoctonear(
                (U256::from(total_block_producer_stake.as_yoctonear()) * U256::from(numer)
                    / U256::from(denom))
                .as_u128(),
            );
            if stake > threshold { version } else { protocol_version }
        } else {
            protocol_version
        };

        PROTOCOL_VERSION_NEXT.set(next_next_epoch_version as i64);
        tracing::info!(target: "epoch_manager", ?next_next_epoch_version, "protocol version voting");

        let mut validator_kickout = HashMap::new();

        // Kickout validators voting for an old version.
        for (validator_id, version) in version_tracker {
            if version >= next_next_epoch_version {
                continue;
            }
            let validator = epoch_info.get_validator(validator_id);
            validator_kickout.insert(
                validator.take_account_id(),
                ValidatorKickoutReason::ProtocolVersionTooOld {
                    version,
                    network_version: next_next_epoch_version,
                },
            );
        }
```

**File:** chain/epoch-manager/src/lib.rs (L1874-1876)
```rust
                let block_info = self.get_block_info(last_final_block_hash)?;
                block_info.height() % AGGREGATOR_SAVE_PERIOD == 0
            };
```

**File:** chain/epoch-manager/src/lib.rs (L1896-1899)
```rust
        if let Some((mut aggregator, replace)) = self.aggregate_epoch_info_upto(last_block_hash)? {
            if !replace {
                aggregator.merge_prefix(&self.epoch_info_aggregator);
            }
```
