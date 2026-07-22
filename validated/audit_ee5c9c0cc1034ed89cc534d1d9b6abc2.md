### Title
`calculate_next_base_gas_price` and `calculate_next_l2_gas_price_for_fin` Always Use `VersionedConstants::latest_constants()` Instead of Block-Version-Specific Constants, Producing Wrong Next-Block L2 Gas Price — (`crates/apollo_consensus_orchestrator/src/fee_market/mod.rs`)

---

### Summary

`calculate_next_l2_gas_price_for_fin` and `calculate_next_base_gas_price` unconditionally call `VersionedConstants::latest_constants()` to obtain `gas_target`, `max_block_size`, and `gas_price_max_change_denominator`. These constants differ materially across the five deployed versions (0.14.0–0.14.4). When the sequencer is producing or validating a block whose Starknet version is not the latest, the EIP-1559 fee-market formula runs with the wrong `gas_target`, yielding a `next_l2_gas_price` that diverges from the protocol-correct value. The wrong price is then committed to the blob and used as the gas price for the following block, directly affecting every fee charged in that block.

---

### Finding Description

`calculate_next_l2_gas_price_for_fin` (line 70) reads:

```rust
let gas_target = VersionedConstants::latest_constants().gas_target;
```

`calculate_next_base_gas_price` (line 92) reads:

```rust
let versioned_constants = VersionedConstants::latest_constants();
// uses versioned_constants.max_block_size (assertion) and
//      versioned_constants.gas_price_max_change_denominator (denominator)
```

`get_min_gas_price_for_height` (line 45) uses `VersionedConstants::latest_constants().min_gas_price` as the fallback when no height-specific entry exists.

The five deployed constant files show that `gas_target` alone varies by more than 3×:

| Version | `gas_target` | `max_block_size` | `min_gas_price` |
|---------|-------------|-----------------|----------------|
| 0.14.0 | 3,200,000,000 | 4,000,000,000 | 3 Gwei |
| 0.14.1 | 4,000,000,000 | 5,000,000,000 | 8 Gwei |
| 0.14.2 | 1,500,000,000 | 5,800,000,000 | 8 Gwei |
| 0.14.3 | 1,040,000,000 | 5,800,000,000 | 8 Gwei |
| 0.14.4 | 1,040,000,000 | 5,800,000,000 | 8 Gwei |

`latest_constants()` always returns the 0.14.4 row. A sequencer producing a block at version 0.14.0 therefore runs the EIP-1559 formula with `gas_target = 1,040,000,000` instead of `3,200,000,000`. Because the formula computes `price_change = (price × |gas_used − gas_target|) / (gas_target × denominator)`, a 3× smaller denominator target inflates the price delta by 3×, driving `next_l2_gas_price` far above the protocol-correct value.

The `max_block_size` assertion (`gas_target < versioned_constants.max_block_size`) also uses the latest value (5.8B), so a `gas_target` that is valid for 0.14.0 (3.2B < 4B) passes the assertion silently even though the wrong denominator is in use.

---

### Impact Explanation

The wrong `next_l2_gas_price` is stored in `FeeMarketInfo.next_l2_gas_price` and propagated as the L2 gas price for the subsequent block. Every transaction in that block has its fee computed against this inflated price. This is a direct, quantifiable economic impact on users: they are charged more (or less, depending on direction of version drift) than the protocol specifies. The impact matches: **"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."**

---

### Likelihood Explanation

The bug is latent during any period when the sequencer binary embeds constants newer than the Starknet version of the block being produced. Version upgrades are routine; the gap between 0.14.0 and 0.14.4 `gas_target` values (3.2B vs 1.04B) is large enough to produce a measurable fee divergence on every block during a version-transition window. No privileged access or malicious input is required; the wrong path is taken unconditionally whenever `latest_constants()` ≠ block-version constants.

---

### Recommendation

Pass the block's `StarknetVersion` into `calculate_next_l2_gas_price_for_fin` and `calculate_next_base_gas_price`, and resolve the constants with `VersionedConstants::get(starknet_version)?` instead of `latest_constants()`. The same fix applies to the fallback in `get_min_gas_price_for_height`. This mirrors the pattern already used in `calculate_block_hash`, which correctly gates `gas_prices_to_hash` on `block_hash_version`.

---

### Proof of Concept

1. Sequencer binary compiled with 0.14.4 constants (`gas_target = 1,040,000,000`).
2. Node configured to produce blocks at Starknet version 0.14.0 (correct `gas_target = 3,200,000,000`).
3. Block N has `l2_gas_used = 2,000,000,000` (below the 0.14.0 target of 3.2B → price should decrease; above the 0.14.4 target of 1.04B → price increases).
4. `calculate_next_l2_gas_price_for_fin` is called:

```rust
// line 70 – always latest, not block-version
let gas_target = VersionedConstants::latest_constants().gas_target; // 1,040,000,000
```

5. `calculate_next_base_gas_price` computes `gas_used (2B) > gas_target (1.04B)` → price **increases**.
6. Protocol-correct result with `gas_target = 3.2B`: `gas_used (2B) < gas_target (3.2B)` → price **decreases**.
7. The committed `next_l2_gas_price` is wrong in both direction and magnitude; every fee in block N+1 is computed against this incorrect value. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_4.json (L1-9)
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
