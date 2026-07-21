### Title
`calculate_next_l2_gas_price_for_fin` and `calculate_next_base_gas_price` unconditionally use `VersionedConstants::latest_constants()` instead of block-version-appropriate constants, producing a wrong committed `next_l2_gas_price` - (File: crates/apollo_consensus_orchestrator/src/fee_market/mod.rs)

---

### Summary

The EIP-1559 fee-market functions `calculate_next_l2_gas_price_for_fin` and `calculate_next_base_gas_price` hard-wire every call to `VersionedConstants::latest_constants()` to obtain `gas_target`, `max_block_size`, and `gas_price_max_change_denominator`. These parameters differ substantially across Starknet versions (e.g., `gas_target` is 3,200,000,000 in v0.14.0 and 1,040,000,000 in v0.14.3 — a 3× difference). Using the latest-version constants regardless of the block being finalized causes the price-adjustment formula to operate on the wrong denominator and target, and the resulting `next_l2_gas_price` is committed verbatim into the `ProposalFin` payload that is broadcast to the network and stored on-chain.

---

### Finding Description

**Root cause — three call sites in the same file all read a global singleton:**

`get_min_gas_price_for_height` (line 45) uses `VersionedConstants::latest_constants().min_gas_price` as the unconditional fallback minimum, ignoring the block's actual Starknet version. [1](#0-0) 

`calculate_next_l2_gas_price_for_fin` (line 70) reads `VersionedConstants::latest_constants().gas_target` as the EIP-1559 target, again ignoring the block's version. [2](#0-1) 

`calculate_next_base_gas_price` (line 92) reads `VersionedConstants::latest_constants()` for both `max_block_size` (used in the guard assertion) and `gas_price_max_change_denominator` (used in the price-change formula). [3](#0-2) 

**The versioned constants differ materially across versions.** The `apollo_versioned_constants` crate defines five distinct versions: [4](#0-3) 

Concrete divergence between the earliest and a mid-range version:

| Field | v0.14.0 | v0.14.3 |
|---|---|---|
| `gas_target` | 3,200,000,000 | 1,040,000,000 |
| `max_block_size` | 4,000,000,000 | 5,800,000,000 |
| `min_gas_price` | 3,000,000,000 (0xb2d05e00) | 8,000,000,000 (0x1dcd65000) | [5](#0-4) [6](#0-5) 

**The wrong value propagates into the committed `ProposalFin`.** `calculate_next_l2_gas_price_for_fin` is called directly inside `get_proposal_content` and its result is placed into `L2GasInfo::next_l2_gas_price_fri` inside the `ProposalFin` that is streamed to the network: [7](#0-6) 

The same path is taken on the validator side via `calculate_next_l2_gas_price`: [8](#0-7) 

**Analog to the external report.** The external bug uses the global gauge weight (`IGaugeController.getGaugeWeight`) instead of the per-user stake. Here, the global `latest_constants()` singleton is used instead of the per-block-version constants. In both cases a single shared value replaces a per-entity value, causing every computation to produce the same (wrong) result regardless of the actual context.

---

### Impact Explanation

The `next_l2_gas_price` committed in `ProposalFin` is the authoritative gas price for the next block. When the block being finalized is at a version whose `gas_target` differs from the latest version's value, the EIP-1559 adjustment formula produces a wrong price:

- If `gas_target` is too small (latest < block version), the price rises faster than the protocol intends, overcharging users.
- If `gas_target` is too large (latest > block version), the price falls faster than intended, undercharging users and potentially depleting fee revenue.

The `max_block_size` guard assertion also uses the wrong value, so a `gas_target` that is valid for the block's actual version may panic or silently pass when it should not.

This is a **Critical** impact: incorrect fee/gas accounting with direct economic effect, committed into a consensus-finalized block.

---

### Likelihood Explanation

The defect is latent during normal single-version operation (sequencer always builds at the latest version). It becomes active at every Starknet protocol upgrade boundary: the block immediately after the version bump is finalized using the new `latest_constants()` while the previous block's gas state was computed under the old constants. Because `gas_target` and `max_block_size` changed by 3× and 1.45× respectively between v0.14.0 and v0.14.3, the price jump at the upgrade boundary is large and deterministic. No privileged access is required; the upgrade itself is the trigger.

---

### Recommendation

Pass the block's `StarknetVersion` into `calculate_next_l2_gas_price_for_fin` and `calculate_next_base_gas_price`, and replace every `VersionedConstants::latest_constants()` call with `VersionedConstants::get(starknet_version)?`:

```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    starknet_version: StarknetVersion,   // <-- add
    l2_gas_used: GasAmount,
    ...
) -> GasPrice {
    let vc = VersionedConstants::get(&starknet_version)
        .unwrap_or_else(|_| VersionedConstants::latest_constants());
    let gas_target = vc.gas_target;
    ...
    calculate_next_base_gas_price(
        current_l2_gas_price, l2_gas_used, gas_target, effective_min, vc,
    )
}
```

Apply the same change to `get_min_gas_price_for_height` so the fallback `min_gas_price` is also version-gated.

---

### Proof of Concept

At a v0.14.0 → v0.14.3 upgrade boundary, `latest_constants()` returns `gas_target = 1,040,000,000`. The block being finalized was built under `gas_target = 3,200,000,000`. Suppose `l2_gas_used = 2,400,000,000` (75 % of the old block size, a normal load):

- **Correct** (v0.14.0 constants): `gas_used < gas_target` → price decreases.
- **Actual** (latest/v0.14.3 constants): `gas_used > gas_target` (2.4B > 1.04B) → price **increases**.

The direction of the price adjustment is inverted. The committed `next_l2_gas_price_fri` in the `ProposalFin` is wrong, and every validator that independently recomputes the value using the same `latest_constants()` will agree on the wrong price — so the error is consensus-consistent but economically incorrect.

### Citations

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L44-51)
```rust
) -> GasPrice {
    let fallback_min_gas_price = VersionedConstants::latest_constants().min_gas_price;
    min_l2_gas_price_per_height
        .iter()
        .rev()
        .find(|e| e.height <= height.0)
        .map(|e| GasPrice(e.price))
        .unwrap_or(fallback_min_gas_price)
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L70-76)
```rust
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L92-101)
```rust
    let versioned_constants = VersionedConstants::latest_constants();
    assert!(
        gas_target < versioned_constants.max_block_size,
        "Gas target must be lower than max block size."
    );
    assert!(gas_target.0 > 0, "Gas target must be greater than zero.");
    assert!(
        versioned_constants.gas_price_max_change_denominator > 0,
        "Denominator constant must be greater than zero."
    );
```

**File:** crates/apollo_versioned_constants/src/lib.rs (L33-43)
```rust
define_versioned_constants!(
    VersionedConstants,
    VersionedConstantsError,
    StarknetVersion::V0_14_0,
    "resources/versioned_constants_diff_regression",
    (V0_14_0, "../resources/orchestrator_versioned_constants_0_14_0.json"),
    (V0_14_1, "../resources/orchestrator_versioned_constants_0_14_1.json"),
    (V0_14_2, "../resources/orchestrator_versioned_constants_0_14_2.json"),
    (V0_14_3, "../resources/orchestrator_versioned_constants_0_14_3.json"),
    (V0_14_4, "../resources/orchestrator_versioned_constants_0_14_4.json"),
);
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_0.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 3200000000,
    "max_block_size": 4000000000,
    "min_gas_price": "0xb2d05e00",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_3.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 1040000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L326-340)
```rust
                let next_l2_gas_price = calculate_next_l2_gas_price_for_fin(
                    args.l2_gas_price,
                    args.build_param.height,
                    info.l2_gas_used,
                    args.override_l2_gas_price_fri,
                    &args.min_l2_gas_price_per_height,
                    args.fee_actual,
                );
                let fin_payload = ProposalFinPayload {
                    commitment_parts: CommitmentParts::from(&info),
                    l2_gas_info: L2GasInfo {
                        next_l2_gas_price_fri: next_l2_gas_price,
                        l2_gas_used: info.l2_gas_used,
                    },
                };
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L427-441)
```rust
    fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
        let fee_actual = compute_fee_actual(
            &self.fee_proposals_window,
            height,
            VersionedConstants::latest_constants().fee_proposal_window_size,
        );
        calculate_next_l2_gas_price_for_fin(
            self.l2_gas_price,
            height,
            l2_gas_used,
            self.config.dynamic_config.override_l2_gas_price_fri,
            &self.config.dynamic_config.min_l2_gas_price_per_height,
            fee_actual,
        )
    }
```
