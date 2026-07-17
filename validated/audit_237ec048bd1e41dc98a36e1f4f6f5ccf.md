### Title
`CongestionInfoV1.allowed_shard` Stored as `u16` While `ShardId` Is `u64` — Silent Truncation Breaks Deadlock-Prevention Invariant After Repeated Resharding - (File: `core/primitives/src/congestion_info.rs`)

### Summary

`CongestionInfoV1.allowed_shard` is a `u16` field embedded in the Borsh-serialized, consensus-critical `CongestionInfo` chunk-header value. `ShardId` is a `u64` newtype. The only conversion path from `ShardId` to `u16` is a silent truncating `as u16` cast. After enough dynamic-resharding splits, shard IDs exceed `u16::MAX`; the stored `allowed_shard` value is then wrong. Every subsequent fully-congested check compares the zero-extended `u16` against the real `u64` shard ID and finds no match, so every sender shard receives `Gas::ZERO` — a protocol-level deadlock that cannot self-heal.

### Finding Description

**Type mismatch in the protocol data structure.**

`CongestionInfoV1` is defined as:

```rust
pub struct CongestionInfoV1 {
    pub delayed_receipts_gas: u128,
    pub buffered_receipts_gas: u128,
    pub receipt_bytes: u64,
    pub allowed_shard: u16,   // ← u16, but ShardId is u64
}
``` [1](#0-0) 

`ShardId` is `u64`:

```rust
pub struct ShardId(u64);
``` [2](#0-1) 

The only `Into<u16>` implementation is a silent truncating cast:

```rust
impl Into<u16> for ShardId {
    fn into(self) -> u16 {
        self.0 as u16   // truncates without any check
    }
}
``` [3](#0-2) 

**Write path — truncation occurs here:**

```rust
pub fn finalize_allowed_shard(&mut self, own_shard: ShardId, all_shards: &[ShardId], congestion_seed: u64) {
    let allowed_shard = Self::get_new_allowed_shard(own_shard, all_shards, congestion_seed);
    self.set_allowed_shard(allowed_shard.into());  // ShardId → u16, truncating
}
``` [4](#0-3) 

**Read path — zero-extension produces a different value:**

```rust
pub fn outgoing_gas_limit(&self, sender_shard: ShardId) -> Gas {
    if Self::is_fully_congested(congestion) {
        if sender_shard == ShardId::from(self.info.allowed_shard()) {  // u16 → u64 zero-extend
            self.config.allowed_shard_outgoing_gas
        } else {
            Gas::ZERO   // ← every shard lands here when IDs > u16::MAX
        }
    }
    ...
}
``` [5](#0-4) 

**How shard IDs grow past `u16::MAX`.**

Dynamic resharding allocates new shard IDs as `max_shard_id + 1` and `max_shard_id + 2`:

```rust
let max_shard_id = *shard_ids.iter().max()...;
let new_shards = vec![max_shard_id + 1, max_shard_id + 2];
``` [6](#0-5) 

After ≥ 32 768 splits from an initial max of 0, shard IDs exceed 65 535. There is no assertion or config-level validation that `max_number_of_shards` stays below `u16::MAX`, and no overflow check in the `Into<u16>` path.

The same truncation also affects `outgoing_size_limit`, which uses `allowed_shard()` to grant the larger `outgoing_receipts_big_size_limit` to the designated shard.

### Impact Explanation

When a shard is fully congested and its `allowed_shard` field has been silently truncated:

- `outgoing_gas_limit` returns `Gas::ZERO` for **every** sender shard, including the one that should be the designated allowed shard.
- `outgoing_size_limit` similarly denies the larger size grant to the correct shard.
- The deadlock-prevention guarantee ("at least one shard can always make progress") is broken at the protocol level.
- The corrupted value is Borsh-serialized into the chunk header and propagated across all validators, so the incorrect `allowed_shard` becomes consensus-finalized state.

### Likelihood Explanation

Under the current `max_number_of_shards` config the threshold of 65 535 splits is not reachable in the near term. However:

1. `max_number_of_shards` is a mutable protocol parameter with no enforced upper bound relative to `u16::MAX`.
2. The `Into<u16>` conversion carries no compile-time or runtime guard; any future increase of the shard-count ceiling silently activates the bug.
3. The `CongestionInfoV1` struct is frozen by Borsh versioning — fixing the field width requires a new `CongestionInfoV2` and a protocol upgrade.

### Recommendation

- Replace `allowed_shard: u16` in `CongestionInfoV1` with a new `CongestionInfoV2` that stores `allowed_shard: u64` (or `ShardId` serialized as `u64`).
- Add a compile-time or runtime assertion in `finalize_allowed_shard` that the chosen `ShardId` fits in `u16` as long as `CongestionInfoV1` is in use.
- Add a protocol-level validation that `max_number_of_shards` ≤ `u16::MAX` while `CongestionInfoV1` is the active version.
- Remove the blanket `impl Into<u16> for ShardId` or replace it with a checked conversion that panics or returns `Result` on overflow.

### Proof of Concept

```
Initial layout: shards [0, 1, 2, 3]  (max = 3)
Split 1: new shards 4, 5  (max = 5)
Split 2: new shards 6, 7  (max = 7)
...
Split 32 766: new shards 65 535, 65 536  (max = 65 536)

finalize_allowed_shard picks ShardId(65 536) as allowed shard.
set_allowed_shard(65_536u64 as u16) → stores 0  (65536 mod 65536 = 0)

outgoing_gas_limit(ShardId(65_536)):
  ShardId::from(self.info.allowed_shard())  →  ShardId::from(0u16)  →  ShardId(0)
  ShardId(65_536) == ShardId(0)  →  false
  → returns Gas::ZERO

outgoing_gas_limit(ShardId(0)):
  ShardId(0) == ShardId(0)  →  true  (wrong shard gets the slot)
  → returns allowed_shard_outgoing_gas

Result: ShardId(0) — a completely unrelated shard — is treated as the
allowed sender, while the actual designated shard gets Gas::ZERO.
All legitimate progress through the congested shard is blocked.
```

The divergent Borsh byte is the `allowed_shard: u16` field in `CongestionInfoV1`, which encodes `0x0000` instead of the correct shard ID, and this value is consensus-finalized in every chunk header produced while the shard remains fully congested.

### Citations

**File:** core/primitives/src/congestion_info.rs (L80-93)
```rust
    pub fn outgoing_gas_limit(&self, sender_shard: ShardId) -> Gas {
        let congestion = self.congestion_level();

        if Self::is_fully_congested(congestion) {
            // Red traffic light: reduce to minimum speed
            if sender_shard == ShardId::from(self.info.allowed_shard()) {
                self.config.allowed_shard_outgoing_gas
            } else {
                Gas::ZERO
            }
        } else {
            mix_gas(self.config.max_outgoing_gas, self.config.min_outgoing_gas, congestion)
        }
    }
```

**File:** core/primitives/src/congestion_info.rs (L360-368)
```rust
    pub fn finalize_allowed_shard(
        &mut self,
        own_shard: ShardId,
        all_shards: &[ShardId],
        congestion_seed: u64,
    ) {
        let allowed_shard = Self::get_new_allowed_shard(own_shard, all_shards, congestion_seed);
        self.set_allowed_shard(allowed_shard.into());
    }
```

**File:** core/primitives/src/congestion_info.rs (L460-470)
```rust
pub struct CongestionInfoV1 {
    /// Sum of gas in currently delayed receipts.
    pub delayed_receipts_gas: u128,
    /// Sum of gas in currently buffered receipts.
    pub buffered_receipts_gas: u128,
    /// Size of borsh serialized receipts stored in state because they
    /// were delayed, buffered, postponed, or yielded.
    pub receipt_bytes: u64,
    /// If fully congested, only this shard can forward receipts.
    pub allowed_shard: u16,
}
```

**File:** core/primitives-core/src/types.rs (L80-80)
```rust
pub struct ShardId(u64);
```

**File:** core/primitives-core/src/types.rs (L180-184)
```rust
impl Into<u16> for ShardId {
    fn into(self) -> u16 {
        self.0 as u16
    }
}
```

**File:** core/primitives/src/shard_layout/v2.rs (L251-253)
```rust
        let max_shard_id =
            *shard_ids.iter().max().expect("there should always be at least one shard");
        let new_shards = vec![max_shard_id + 1, max_shard_id + 2];
```
