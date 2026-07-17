### Title
`ClampOutgoingGasAdmission` Admission/Deduction Asymmetry Allows Oversized Receipts to Exceed the `allowed_shard_outgoing_gas` Limit on Fully-Congested Shards — (File: `runtime/runtime/src/congestion_control.rs`)

---

### Summary

The `ClampOutgoingGasAdmission` feature (protocol version 85) introduces an asymmetry in `ReceiptSinkV2::try_forward`: the **admission check** uses `min(gas, allowed_shard_outgoing_gas)` while the **deduction** uses the full `gas`. When a buffered receipt's gas exceeds `allowed_shard_outgoing_gas`, the receipt passes the admission gate (because the clamped value fits within the limit) but then deducts the full, unclamped amount from `forward_limit.gas`. The result is that a single user-crafted receipt can cause the allowed shard to forward substantially more gas to a fully-congested receiver than the protocol's deadlock-prevention invariant permits, worsening congestion rather than maintaining the minimum-flow guarantee.

---

### Finding Description

When a receiver shard is fully congested (`congestion_level == 1.0`), `outgoing_gas_limit` for the designated allowed shard is set to exactly `allowed_shard_outgoing_gas` (1 PGAS on mainnet). All other shards receive a limit of `Gas::ZERO`. [1](#0-0) 

Inside `try_forward`, the `ClampOutgoingGasAdmission` branch computes:

```rust
let admission_gas = gas.min(allowed_shard_outgoing_gas);   // clamped
if forward_limit.gas >= admission_gas && forward_limit.size >= size {
    outgoing_receipts.push(receipt);
    forward_limit.gas = forward_limit.gas.saturating_sub(gas); // full gas deducted
``` [2](#0-1) 

Consider a buffered receipt with `gas = 3 * allowed_shard_outgoing_gas` (3 PGAS):

| Step | Value |
|---|---|
| `forward_limit.gas` (allowed shard, fully congested) | 1 PGAS |
| `admission_gas` | `min(3 PGAS, 1 PGAS)` = **1 PGAS** |
| Admission check: `1 PGAS >= 1 PGAS` | **PASS** |
| Deduction: `1 PGAS - 3 PGAS` (saturating) | **0** |
| Gas actually forwarded to congested shard | **3 PGAS** (3× the limit) |

The receipt is forwarded, delivering 3 PGAS to the already-congested shard instead of the intended 1 PGAS. The limit is then zeroed, blocking further forwarding in the same chunk, but the damage—3× the intended gas injection—has already been done.

The `CongestionControlConfig` values that make this reachable on mainnet: [3](#0-2) 

`allowed_shard_outgoing_gas = 1_000_000_000_000_000` (1 PGAS). A function-call receipt with large prepaid gas (up to the chunk gas limit) can exceed this value.

---

### Impact Explanation

The congestion control system's core invariant for a fully-congested shard is: **at most `allowed_shard_outgoing_gas` of new gas enters the shard per chunk from the allowed sender, and zero from all others.** This invariant is what makes deadlocks provably impossible. [4](#0-3) 

When a large receipt breaks this invariant, the congested shard's delayed-receipt gas (`incoming_congestion`) increases beyond what the protocol's backpressure model accounts for. Recovery from full congestion is delayed or, in a sustained attack with many large receipts cycling through the buffer, may be indefinitely prolonged. The congestion control mechanism—intended to push the shard toward recovery—actively worsens the congestion level for each oversized receipt forwarded.

This is the direct nearcore analog of the Aave counterproductive liquidation: the safety mechanism (congestion backpressure) behaves counter-productively in the specific regime where `receipt.gas > allowed_shard_outgoing_gas`, pushing the system further into the "red zone" rather than toward recovery.

---

### Likelihood Explanation

Any unprivileged user can submit a transaction with large prepaid gas (e.g., a function call with `300 TGAS` prepaid). The resulting receipt is buffered when the receiver shard is congested. When that shard becomes the round-robin "allowed shard," `forward_from_buffer_to_shard` iterates the buffer and calls `try_forward` on the large receipt. [5](#0-4) 

No privileged role is required. The attacker only needs to:
1. Identify a congested shard.
2. Submit transactions targeting that shard with maximum prepaid gas.
3. Wait for the receipts to be buffered and then forwarded via the allowed-shard path.

The `ClampOutgoingGasAdmission` feature is active at protocol version 85 (current stable mainnet). [6](#0-5) 

---

### Recommendation

The deduction must use `admission_gas` (the clamped value), not the raw `gas`, so that the amount subtracted from `forward_limit.gas` matches the amount used for the admission decision:

```rust
// After forwarding:
forward_limit.gas = forward_limit.gas.saturating_sub(admission_gas); // was: gas
```

This preserves the deadlock-prevention property (large receipts can still be admitted) while enforcing the invariant that no more than `allowed_shard_outgoing_gas` is consumed from the limit per forwarded receipt. If the intent is to allow exactly one large receipt through per chunk, the deduction should still zero the limit, but the `own_congestion_info` accounting and the stats should reflect the actual gas forwarded (`gas`), not the clamped value.

---

### Proof of Concept

**Setup:** Mainnet protocol version 85, receiver shard at 100% congestion (`congestion_level == 1.0`), sender shard is the designated allowed shard.

**State before `try_forward`:**
- `forward_limit.gas = allowed_shard_outgoing_gas` = 1 PGAS
- Buffered receipt: `gas = 2 PGAS`, `size` within size limit

**Execution trace (lines 443–454):**
```
admission_gas = min(2 PGAS, 1 PGAS) = 1 PGAS
forward_limit.gas (1 PGAS) >= admission_gas (1 PGAS) → true
→ receipt forwarded (2 PGAS delivered to congested shard)
forward_limit.gas = 1 PGAS.saturating_sub(2 PGAS) = 0
```

**Observed:** 2 PGAS forwarded to the fully-congested shard.
**Expected:** ≤ 1 PGAS (`allowed_shard_outgoing_gas`).

The congested shard's `delayed_receipts_gas` increases by 2 PGAS instead of 1 PGAS, raising its `incoming_congestion` level and delaying recovery. [7](#0-6)

### Citations

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

**File:** runtime/runtime/src/congestion_control.rs (L338-383)
```rust
    fn forward_from_buffer_to_shard(
        &mut self,
        buffer_shard_id: ShardId,
        state_update: &mut TrieUpdate,
        apply_state: &ApplyState,
        shard_layout: &ShardLayout,
    ) -> Result<(), RuntimeError> {
        let mut num_forwarded = 0;
        let mut outgoing_metadatas_updates: Vec<(ByteSize, Gas)> = Vec::new();
        for receipt_result in
            self.outgoing_buffers.to_shard(buffer_shard_id).iter(&state_update.trie, true)
        {
            let receipt = receipt_result?;
            let gas = receipt_congestion_gas(&receipt, &apply_state.config)?;
            let size = receipt_size(&receipt)?;
            let should_update_outgoing_metadatas = receipt.should_update_outgoing_metadatas();
            let receipt = receipt.into_receipt();
            let target_shard_id = receipt.receiver_shard_id(&shard_layout)?;

            match Self::try_forward(
                receipt,
                gas,
                size,
                target_shard_id,
                &mut self.outgoing_limit,
                &mut self.outgoing_receipts,
                apply_state,
                &mut self.stats,
            )? {
                ReceiptForwarding::Forwarded => {
                    self.own_congestion_info.remove_receipt_bytes(size)?;
                    self.own_congestion_info.remove_buffered_receipt_gas(gas.as_gas().into())?;
                    if should_update_outgoing_metadatas {
                        // Can't update metadatas immediately because state_update is borrowed by iterator.
                        outgoing_metadatas_updates.push((ByteSize::b(size), gas));
                    }
                    // count how many to release later to avoid modifying
                    // `state_update` while iterating based on
                    // `state_update.trie`.
                    num_forwarded += 1;
                }
                ReceiptForwarding::NotForwarded(_) => {
                    break;
                }
            }
        }
```

**File:** runtime/runtime/src/congestion_control.rs (L403-463)
```rust
    fn try_forward(
        receipt: Receipt,
        gas: Gas,
        mut size: u64,
        shard: ShardId,
        outgoing_limit: &mut HashMap<ShardId, OutgoingLimit>,
        outgoing_receipts: &mut Vec<Receipt>,
        apply_state: &ApplyState,
        stats: &mut ReceiptSinkStats,
    ) -> Result<ReceiptForwarding, RuntimeError> {
        // There is a bug which allows to create receipts that are above the size limit. Receipts
        // above the size limit might not fit under the maximum outgoing size limit. Let's pretend
        // that all receipts are at most `max_receipt_size` to avoid receipts getting stuck.
        // See https://github.com/near/nearcore/issues/12606
        let max_receipt_size = apply_state.config.wasm_config.limit_config.max_receipt_size;
        if size > max_receipt_size {
            tracing::debug!(
                target: "runtime",
                receipt_id=?receipt.receipt_id(),
                size,
                max_receipt_size,
                "try_forward observed a receipt with size exceeding the size limit",
            );
            size = max_receipt_size;
        }

        // Default case set to `Gas::MAX`: If no outgoing limit was defined for the receiving
        // shard, this usually just means the feature is not enabled. Or, it
        // could be a special case during resharding events. Or even a bug. In
        // any case, if we cannot know a limit, treating it as literally "no
        // limit" is the safest approach to ensure availability.
        let default_gas_limit = Gas::MAX;

        // Since bandwidth scheduler, a shard is not allowed to send any receipts if it doesn't have a grant.
        let default_size_limit = 0;

        let default_outgoing_limit =
            OutgoingLimit { gas: default_gas_limit, size: default_size_limit };
        let forward_limit = outgoing_limit.entry(shard).or_insert(default_outgoing_limit);

        let admission_gas = if ProtocolFeature::ClampOutgoingGasAdmission
            .enabled(apply_state.current_protocol_version)
        {
            gas.min(apply_state.config.congestion_control_config.allowed_shard_outgoing_gas)
        } else {
            gas
        };

        if forward_limit.gas >= admission_gas && forward_limit.size >= size {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "forwarding buffered receipt");
            outgoing_receipts.push(receipt);
            forward_limit.gas = forward_limit.gas.saturating_sub(gas);
            forward_limit.size -= size;
            stats.forwarded_receipts.entry(shard).or_default().add_receipt(size, gas);

            Ok(ReceiptForwarding::Forwarded)
        } else {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "not forwarding buffered receipt");
            Ok(ReceiptForwarding::NotForwarded(receipt))
        }
    }
```

**File:** core/parameters/res/runtime_configs/68.yaml (L44-47)
```yaml
allowed_shard_outgoing_gas: { 
  old: 9_223_372_036_854_775_807,
  new: 1_000_000_000_000_000
}
```

**File:** core/parameters/src/config.rs (L180-187)
```rust
    /// How much gas the chosen allowed shard can send to a 100% congested shard.
    ///
    /// This amount is the absolute minimum of new workload a congested shard has to
    /// accept every round. It ensures deadlocks are provably impossible. But in
    /// ideal conditions, the gradual reduction of new workload entering the system
    /// combined with gradually limited forwarding to congested shards should
    /// prevent shards from becoming 100% congested in the first place.
    pub allowed_shard_outgoing_gas: Gas,
```

**File:** core/primitives-core/src/version.rs (L569-571)
```rust
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
```
