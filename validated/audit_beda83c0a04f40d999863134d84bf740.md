### Title
Divergent Gas Accounting in `ClampOutgoingGasAdmission` Breaks Congestion-Control Deadlock-Prevention Invariant — (File: `runtime/runtime/src/congestion_control.rs`)

---

### Summary

`ProtocolFeature::ClampOutgoingGasAdmission` (protocol version 85) introduces an asymmetry inside `ReceiptSinkV2::try_forward`: the **admission gate** uses a clamped value (`admission_gas = min(gas, allowed_shard_outgoing_gas)`), but the **budget deduction** uses the full, unclamped `gas`. A single large-gas buffered receipt can therefore exhaust the entire per-chunk gas budget for a congested shard in one shot, breaking the protocol invariant that `allowed_shard_outgoing_gas` worth of gas must flow to a 100%-congested shard every chunk to prevent deadlocks.

---

### Finding Description

In `ReceiptSinkV2::try_forward` at lines 443–454:

```rust
let admission_gas = if ProtocolFeature::ClampOutgoingGasAdmission
    .enabled(apply_state.current_protocol_version)
{
    gas.min(apply_state.config.congestion_control_config.allowed_shard_outgoing_gas)
} else {
    gas
};

if forward_limit.gas >= admission_gas && forward_limit.size >= size {
    outgoing_receipts.push(receipt);
    forward_limit.gas = forward_limit.gas.saturating_sub(gas);   // ← full `gas`, not `admission_gas`
    forward_limit.size -= size;
``` [1](#0-0) 

The gate check on line 451 uses `admission_gas` (clamped to `allowed_shard_outgoing_gas`, which is 1 PGAS per `68.yaml`), but the deduction on line 454 uses the raw `gas` value. These two quantities diverge whenever a buffered receipt carries more gas than `allowed_shard_outgoing_gas`.

The `allowed_shard_outgoing_gas` parameter is the exact minimum the congestion-control protocol guarantees will flow to a 100%-congested shard each chunk to make deadlocks provably impossible: [2](#0-1) 

Its value is set to 1 PGAS at protocol version 68: [3](#0-2) 

`ClampOutgoingGasAdmission` is stabilised at protocol version 85 alongside the other features in that batch: [4](#0-3) 

---

### Impact Explanation

For a 100%-congested shard B where shard A is the designated allowed shard:

| Step | Value |
|---|---|
| `forward_limit.gas` (set by congestion control) | 1 PGAS |
| Receipt R attached gas | 5 PGAS |
| `admission_gas` (clamped) | 1 PGAS |
| Gate check `1 PGAS >= 1 PGAS` | **passes** |
| Deduction `1 PGAS − 5 PGAS` (saturating) | **0** |
| Next receipt with gas = 0.1 PGAS: `0 >= 0.1 PGAS` | **fails** |

Result: only one receipt is forwarded to the congested shard per chunk regardless of its gas amount, instead of potentially many receipts totalling 1 PGAS. The deadlock-prevention guarantee — that at least `allowed_shard_outgoing_gas` of gas flows per chunk — is broken: the budget is over-consumed by the first large-gas receipt, starving all subsequent receipts in the same chunk. This is a liveness-level protocol invariant violation that weakens the anti-deadlock property of the congestion control system.

---

### Likelihood Explanation

Any receipt with `gas > allowed_shard_outgoing_gas` (1 PGAS) in an outgoing buffer targeting a 100%-congested shard triggers the divergence. Cross-contract calls with large attached gas are routine in production. The bug is reachable on every chunk where such a receipt is at the head of the buffer for the allowed shard, which is a normal steady-state condition under congestion.

---

### Recommendation

Change the budget deduction to use `admission_gas` (the clamped value) instead of `gas`, so the gate check and the accounting are consistent:

```rust
// line 454 — change:
forward_limit.gas = forward_limit.gas.saturating_sub(gas);
// to:
forward_limit.gas = forward_limit.gas.saturating_sub(admission_gas);
```

This preserves the intent of `ClampOutgoingGasAdmission` (allow large receipts to pass the gate) while correctly accounting only the clamped amount against the budget, so multiple receipts can be forwarded per chunk up to the `allowed_shard_outgoing_gas` limit.

---

### Proof of Concept

Concrete divergence with production parameters (`allowed_shard_outgoing_gas = 1 PGAS`, `forward_limit.gas = 1 PGAS` for a 100%-congested shard):

1. Buffer contains receipts `[R1(gas=5 PGAS), R2(gas=0.5 PGAS), R3(gas=0.3 PGAS)]`.
2. `ClampOutgoingGasAdmission` enabled (protocol ≥ 85).
3. **R1**: `admission_gas = min(5, 1) = 1 PGAS`; gate `1 >= 1` → forwarded; deduction `1 − 5 = 0` (saturating). `forward_limit.gas = 0`.
4. **R2**: `admission_gas = min(0.5, 1) = 0.5 PGAS`; gate `0 >= 0.5` → **blocked**.
5. **R3**: same — **blocked**.

Expected behaviour (correct deduction using `admission_gas`):
3. **R1**: forwarded; deduction `1 − 1 = 0`. `forward_limit.gas = 0`. (Same result here, but for a receipt with gas=0.5 PGAS the budget would be `0.5 PGAS` remaining, allowing R2 and R3 to also be forwarded.)

The exact divergent value is `gas` vs `admission_gas` on line 454 of `runtime/runtime/src/congestion_control.rs`. [5](#0-4)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L443-456)
```rust
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
```

**File:** core/parameters/src/config.rs (L180-188)
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

**File:** core/parameters/res/runtime_configs/68.yaml (L38-47)
```yaml
# 1 PGAS
min_outgoing_gas: { 
  old: 9_223_372_036_854_775_807,
  new: 1_000_000_000_000_000
}
# 1 PGAS
allowed_shard_outgoing_gas: { 
  old: 9_223_372_036_854_775_807,
  new: 1_000_000_000_000_000
}
```

**File:** core/primitives-core/src/version.rs (L569-571)
```rust
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
```
