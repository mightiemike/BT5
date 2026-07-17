### Title
Silent `ShardId`→`u16` Truncation in `CongestionInfoV1.allowed_shard` Breaks Congestion-Control Deadlock Prevention at DynamicResharding Boundary — (File: `core/primitives/src/congestion_info.rs`)

---

### Summary

`CongestionInfo::finalize_allowed_shard()` converts a `ShardId` (a `u64` newtype) to `u16` via a silent truncating `as` cast when writing into the Borsh-serialized, protocol-level `CongestionInfoV1.allowed_shard` field. Once `DynamicResharding` (protocol version 85) assigns a shard ID whose lower 16 bits collide with a different shard, the stored `allowed_shard` value is wrong. The congestion-control "allowed shard" mechanism then grants forwarding rights to the wrong shard and permanently blocks the correct one, breaking the deadlock-prevention guarantee that is the entire purpose of the field.

---

### Finding Description

**Type chain:**

`ShardId` is declared as `struct ShardId(u64)`. [1](#0-0) 

`CongestionInfoV1.allowed_shard` is stored as `u16` in the Borsh-serialized chunk-header field. [2](#0-1) 

`Into<u16> for ShardId` is implemented as a bare `as u16` cast — no bounds check, no panic, silent truncation: [3](#0-2) 

**Call site:**

`CongestionInfo::finalize_allowed_shard()` selects the allowed shard from the live shard layout and immediately calls `self.set_allowed_shard(allowed_shard.into())`, invoking the truncating `Into<u16>`: [4](#0-3) 

This is called every chunk in `validate_apply_state_update`: [5](#0-4) 

And also during resharding in `ReshardingManager::finalize_allowed_shard`: [6](#0-5) 

**Consumption site:**

`CongestionControl::outgoing_gas_limit()` reads back the stored `u16`, widens it to `ShardId` via `ShardId::from(u16)`, and compares it against the actual sender shard: [7](#0-6) 

If the original shard ID was, for example, `0x10001` (65537), it is stored as `0x0001` (1). The comparison `sender_shard == ShardId::from(self.info.allowed_shard())` then matches shard 1 instead of shard 65537. Shard 65537 is permanently blocked; shard 1 receives undeserved forwarding rights.

**Protocol boundary:**

`DynamicResharding` is stable at protocol version 85. `ShardLayoutV2` and `ShardLayoutV3` assign shard IDs as arbitrary `u64` values, not constrained to `0..num_shards`. The `allowed_shard: u16` field is Borsh-serialized as part of `CongestionInfo::V1` in every chunk header. [8](#0-7) [9](#0-8) 

The `TrieKey::BufferedReceipt` path does have an explicit `assert!` for the same overflow, but it only fires when buffered receipts exist to that shard. `finalize_allowed_shard` is called unconditionally every chunk and has no such guard. [10](#0-9) 

---

### Impact Explanation

`CongestionInfoV1.allowed_shard` is the sole mechanism that guarantees liveness under full congestion: exactly one shard is permitted to forward receipts to a fully-congested shard, preventing deadlock. A wrong value means:

1. The shard that should be allowed is blocked (receives `Gas::ZERO` forwarding allowance).
2. A different, unrelated shard receives the forwarding allowance it did not earn.
3. If the correct shard is the only one with pending receipts for the congested shard, the system deadlocks — no receipts drain, congestion never clears.

Because all nodes compute the same truncated value deterministically, consensus is not broken, but the protocol-level invariant ("the allowed shard can always make progress") is permanently violated for any shard ID ≥ 65536.

**Scope: High** — liveness failure / permanent congestion deadlock on affected shards.

---

### Likelihood Explanation

Current mainnet shard IDs are small integers (0–5 range), so the truncation does not trigger today. The likelihood increases as `DynamicResharding` is exercised: shard IDs in `ShardLayoutV2`/`V3` are assigned from a counter that can grow without bound across multiple resharding events. No privileged action is required once the protocol assigns a shard ID ≥ 65536; the truncation occurs automatically on every chunk application.

---

### Recommendation

Replace the silent `as u16` cast in `Into<u16> for ShardId` with a checked conversion, or — preferably — widen `CongestionInfoV1.allowed_shard` from `u16` to `u64` in a new `CongestionInfoV2` version. The `u16` width was chosen to reduce trie depth (as noted in `TrieKey::BufferedReceipt`), but the same comment acknowledges the constraint. At minimum, add a `u16::try_from(self.0).expect(...)` in `finalize_allowed_shard` so that an out-of-range shard ID panics loudly rather than silently corrupting the protocol field.

---

### Proof of Concept

```
Given: DynamicResharding assigns shard ID = 65537 (0x10001).

finalize_allowed_shard() selects ShardId(65537) as the allowed shard.
set_allowed_shard(ShardId(65537).into())
  → Into<u16> for ShardId: 65537u64 as u16 = 1u16   ← TRUNCATION
  → CongestionInfoV1 { allowed_shard: 1 } is Borsh-serialized into chunk header.

outgoing_gas_limit(sender_shard = ShardId(65537)):
  ShardId::from(self.info.allowed_shard())  // from(1u16) = ShardId(1)
  ShardId(65537) == ShardId(1)  → false
  → returns Gas::ZERO            ← correct shard is blocked

outgoing_gas_limit(sender_shard = ShardId(1)):
  ShardId(1) == ShardId(1)  → true
  → returns allowed_shard_outgoing_gas  ← wrong shard gets forwarding rights

Result: shard 65537 cannot drain receipts to the congested shard.
        If shard 65537 is the only sender, the system deadlocks.
``` [4](#0-3) [3](#0-2) [7](#0-6)

### Citations

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

**File:** core/primitives/src/congestion_info.rs (L80-92)
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
```

**File:** core/primitives/src/congestion_info.rs (L186-189)
```rust
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub enum CongestionInfo {
    V1(CongestionInfoV1),
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

**File:** core/primitives/src/congestion_info.rs (L468-469)
```rust
    /// If fully congested, only this shard can forward receipts.
    pub allowed_shard: u16,
```

**File:** runtime/runtime/src/lib.rs (L2711-2716)
```rust
        let congestion_seed = apply_state.block_height.wrapping_add(shard_seed);
        own_congestion_info.finalize_allowed_shard(
            apply_state.shard_id,
            &all_shards,
            congestion_seed,
        );
```

**File:** chain/chain/src/resharding/manager.rs (L367-389)
```rust
    fn finalize_allowed_shard(
        child_shard_layout: &ShardLayout,
        child_shard_uid: &ShardUId,
        congestion_info: &mut CongestionInfo,
    ) -> Result<(), Error> {
        let all_shards = child_shard_layout.shard_ids().collect_vec();
        let own_shard = child_shard_uid.shard_id();
        let own_shard_index = child_shard_layout
            .get_shard_index(own_shard)?
            .try_into()
            .expect("ShardIndex must fit in u64");
        // Please note that the congestion seed used during resharding is
        // different than the one used during normal operation. In runtime the
        // seed is set to the sum of shard index and block height. The block
        // height isn't easily available on all call sites which is why the
        // simplified seed is used. This is valid because it's deterministic and
        // resharding is a very rare event. However in a perfect world it should
        // be the same.
        // TODO - Use proper congestion control seed during resharding.
        let congestion_seed = own_shard_index;
        congestion_info.finalize_allowed_shard(own_shard, &all_shards, congestion_seed);
        Ok(())
    }
```

**File:** core/primitives-core/src/version.rs (L560-571)
```rust
            | ProtocolFeature::DynamicResharding
            | ProtocolFeature::StickyReshardingValidatorAssignment
            | ProtocolFeature::StrictNonce
            | ProtocolFeature::PostQuantumSignatures
            | ProtocolFeature::UniqueChunkTransactions
            | ProtocolFeature::ValidateBlockOrdinalAndEpochSyncDataHash
            | ProtocolFeature::YieldWithId
            | ProtocolFeature::ExecutionMetadataV4
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
```

**File:** core/primitives/src/trie_key.rs (L526-534)
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
```
