### Title
`TrieKey::BufferedReceipt` Encodes `ShardId` as `u16` with a Panic-Only Guard, Causing Node Crash on Any `ShardId > 65535` Under Dynamic Resharding - (File: `core/primitives/src/trie_key.rs`)

---

### Summary

`TrieKey::BufferedReceipt` stores the `receiving_shard` field as a `u16` in the trie key, but `ShardId` is a `u64`. The only guard is a runtime `assert!` that panics when `ShardId > 65535`. Under the `DynamicResharding` protocol feature (stabilized at protocol version 85), shard IDs are assigned as monotonically increasing `u64` values starting from the current maximum. After enough resharding events, any shard whose numeric ID exceeds `65535` will cause every node that tries to buffer a receipt to that shard to **panic and crash**. This is a liveness-breaking invariant violation embedded in the trie key encoding.

---

### Finding Description

In `core/primitives/src/trie_key.rs`, the `TrieKey::BufferedReceipt` variant holds a `receiving_shard: ShardId` (a `u64` newtype). When `append_into` serializes this key into the trie, it explicitly truncates the shard ID to `u16`:

```rust
// core/primitives/src/trie_key.rs:526-534
TrieKey::BufferedReceipt { index, receiving_shard } => {
    let receiving_shard = *receiving_shard;
    buf.push(col::BUFFERED_RECEIPT);
    // Use  u16 for shard id to reduce depth in trie.
    let receiving_shard: u64 = receiving_shard.into();
    assert!(receiving_shard <= u16::MAX as u64, "Shard ID too big.");
    let receiving_shard: u16 = receiving_shard as u16;
    buf.extend(&receiving_shard.to_le_bytes());
    buf.extend(&index.to_le_bytes());
}
```

The `len()` method for this variant also hardcodes `size_of::<u16>()` as the shard-id width:

```rust
// core/primitives/src/trie_key.rs:412-416
TrieKey::BufferedReceipt { index, .. } => {
    col::BUFFERED_RECEIPT.len()
        + std::mem::size_of::<u16>()
        + std::mem::size_of_val(index)
}
```

`ShardId` is a `u64` newtype. Under `DynamicResharding` (protocol feature at version 85), each resharding event derives new shard IDs as `max_shard_id + 1` and `max_shard_id + 2`:

```rust
// core/primitives/src/shard_layout/v2.rs:251-253
let max_shard_id =
    *shard_ids.iter().max().expect("there should always be at least one shard");
let new_shards = vec![max_shard_id + 1, max_shard_id + 2];
```

The same pattern exists in `ShardLayoutV3::derive_impl`. Starting from the current mainnet shard IDs (small integers), after approximately 32,768 resharding events the maximum shard ID would exceed `u16::MAX = 65535`. At that point, any attempt to buffer a receipt to that shard calls `append_into`, hits the `assert!`, and **panics the node process**.

The `Into<u16>` conversion on `ShardId` is also silently truncating:

```rust
// core/primitives-core/src/types.rs:180-183
impl Into<u16> for ShardId {
    fn into(self) -> u16 {
        self.0 as u16
    }
}
```

This silent truncation is used in other contexts (e.g., `congestion_info.set_allowed_shard(receiver_shard.into())`), meaning a shard ID above `65535` would silently alias to a different shard ID in congestion control, corrupting the congestion state before the panic even fires.

---

### Impact Explanation

- **Liveness**: Any node that processes a chunk containing a receipt destined for a shard with `ShardId > 65535` will panic at `assert!(receiving_shard <= u16::MAX as u64, "Shard ID too big.")`. This crashes the node process. Since all validators process the same chunks, this is a **network-wide halt** once the threshold is crossed.
- **State corruption (pre-panic)**: The `Into<u16>` truncation on `ShardId` means that before the panic fires in `append_into`, other code paths using `.into()` for `u16` will silently map shard IDs above `65535` to wrong shard IDs, corrupting congestion-control state and receipt routing.
- **Trie key collision**: Two distinct shard IDs that differ only in their upper 48 bits (e.g., `ShardId(65536)` and `ShardId(0)`) would produce identical `BufferedReceipt` trie keys, causing receipt data for one shard to overwrite another's.

---

### Likelihood Explanation

With `DynamicResharding` active, shard IDs grow monotonically. The current mainnet has shard IDs in the single digits. Reaching `65535` requires ~32,000 resharding events, which is not imminent. However:

1. The `DynamicResharding` feature is already stabilized at protocol version 85.
2. The `ShardLayoutV3::derive` and `ShardLayoutV2::derive` functions unconditionally increment the max shard ID with no upper-bound check.
3. There is no protocol-level cap on the number of resharding events.
4. The `Into<u16>` silent truncation is a latent correctness bug that activates before the panic threshold.

The bug is a **hard protocol invariant violation** with no graceful degradation — the node panics rather than returning an error.

---

### Recommendation

1. **Immediate**: Change the `BufferedReceipt` trie key encoding to use `u64` (8 bytes) for the shard ID instead of `u16`. This is a **trie schema migration** requiring a protocol version bump, since existing trie keys on-disk use the 2-byte encoding.
2. **Short term**: Replace the `assert!` with a `Result`-returning error so the failure is recoverable rather than a process crash.
3. **Short term**: Remove or make checked the `Into<u16> for ShardId` impl, replacing it with an explicit `try_into()` that returns an error on overflow.
4. **Long term**: Add a protocol-level cap on the maximum shard ID value, or redesign shard ID assignment to use a compact index rather than a monotonically growing counter.

---

### Proof of Concept

The exact divergent bytes:

- **Current encoding** of `TrieKey::BufferedReceipt { receiving_shard: ShardId(65536), index: 0 }`:
  - `assert!` fires → **node panics**
- **Current encoding** of `TrieKey::BufferedReceipt { receiving_shard: ShardId(65537), index: 0 }` via the `Into<u16>` path (if the assert were absent):
  - Encodes as `[col::BUFFERED_RECEIPT, 0x01, 0x00, 0,0,0,0,0,0,0,0]` — identical to `ShardId(1)`, causing a **trie key collision**

The root cause is at: [1](#0-0) 

The `u16` width assumption in `len()`: [2](#0-1) 

The silent `Into<u16>` truncation on `ShardId`: [3](#0-2) 

The unbounded shard ID growth in `DynamicResharding`: [4](#0-3) [5](#0-4)

### Citations

**File:** core/primitives/src/trie_key.rs (L412-416)
```rust
            TrieKey::BufferedReceipt { index, .. } => {
                col::BUFFERED_RECEIPT.len()
                    + std::mem::size_of::<u16>()
                    + std::mem::size_of_val(index)
            }
```

**File:** core/primitives/src/trie_key.rs (L526-535)
```rust
            TrieKey::BufferedReceipt { index, receiving_shard } => {
                let receiving_shard = *receiving_shard;
                buf.push(col::BUFFERED_RECEIPT);
                // Use  u16 for shard id to reduce depth in trie.
                let receiving_shard: u64 = receiving_shard.into();
                assert!(receiving_shard <= u16::MAX as u64, "Shard ID too big.");
                let receiving_shard: u16 = receiving_shard as u16;
                buf.extend(&receiving_shard.to_le_bytes());
                buf.extend(&index.to_le_bytes());
            }
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

**File:** core/primitives/src/shard_layout/v3.rs (L271-273)
```rust
        let max_shard_id =
            *shard_ids.iter().max().expect("there should always be at least one shard");
        let new_shards = vec![max_shard_id + 1, max_shard_id + 2];
```
