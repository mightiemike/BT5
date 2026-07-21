### Title
`max_block_size` / `receipt_l2_gas` Config Invariant Not Enforced at Runtime Causes Incorrect EIP-1559 Fee Market Calibration - (File: crates/apollo_versioned_constants/src/lib.rs)

### Summary
The EIP-1559 fee market uses `max_block_size` from `apollo_versioned_constants::VersionedConstants` — a versioned constant that changes automatically with each Starknet protocol version upgrade — to calibrate gas price adjustments. The blockifier's `Bouncer` uses `receipt_l2_gas` from `BouncerConfig` — a static deployment config that must be manually updated — to enforce the actual block capacity. These two values must be equal, as documented in code comments, but no runtime validation enforces this invariant. When they diverge across a version boundary, the fee market computes systematically wrong gas prices, producing an incorrect economic signal for all network participants.

### Finding Description

Two separate constants represent the same logical concept (maximum block gas capacity) in two different subsystems:

**1. `apollo_versioned_constants::VersionedConstants::max_block_size`**
Loaded from version-specific JSON files by the consensus orchestrator. This value is baked into the binary and changes automatically with each Starknet version upgrade. [1](#0-0) 

**2. `blockifier::bouncer::BouncerWeights::receipt_l2_gas`**
A field in `BouncerConfig.block_max_capacity`, loaded from the batcher's static deployment config. This value must be manually updated when `max_block_size` changes. [2](#0-1) 

The code explicitly documents the invariant with comments but provides no enforcement: [3](#0-2) [4](#0-3) 

The fee market's `calculate_next_l2_gas_price_for_fin` reads `gas_target` from `VersionedConstants::latest_constants()` (which is a fraction of `max_block_size`) and passes it to `calculate_next_base_gas_price`. The `l2_gas_used` argument is the actual gas consumed in the block, which is bounded by `receipt_l2_gas` (enforced by the bouncer). When these two constants diverge, the fee market's model of block fullness is wrong. [5](#0-4) [6](#0-5) 

**Historical evidence of divergence risk:** `max_block_size` has changed three times across five versions: [7](#0-6) [8](#0-7) [9](#0-8) 

Each change requires a manual update to `receipt_l2_gas` in the batcher config. The `replacer_batcher_config.json` exposes `receipt_l2_gas` as a template variable filled in at deployment time independently of the versioned constants: [10](#0-9) 

The `calculate_next_base_gas_price` function asserts `gas_target < max_block_size` but does not assert that `max_block_size == receipt_l2_gas`: [11](#0-10) 

### Impact Explanation

**Case 1 — `max_block_size` increased by version upgrade, `receipt_l2_gas` not updated:**
- The bouncer closes blocks at the old (smaller) `receipt_l2_gas` limit.
- The fee market computes price adjustments relative to the new (larger) `gas_target = fraction × max_block_size`.
- Since `l2_gas_used ≤ receipt_l2_gas < gas_target`, the fee market always sees blocks as "underfull."
- Gas prices decrease even when blocks are actually full, leading to underpriced gas and economic loss for sequencers.

**Case 2 — `receipt_l2_gas` increased, `max_block_size` not updated:**
- Blocks can fill up beyond `max_block_size`.
- The fee market sees `l2_gas_used > gas_target` more aggressively, overpricing gas.

This is "Incorrect fee, gas, bouncer, resource accounting with economic impact."

### Likelihood Explanation

Medium. `max_block_size` has changed three times across five versions (0.14.0 → 4 B, 0.14.1 → 5 B, 0.14.2 → 5.8 B). Each version upgrade requires a manual update to `receipt_l2_gas` in the batcher config. The `replacer_batcher_config.json` exposes this as a template variable, creating an additional deployment-time risk. There is no automated check or startup validation to catch the mismatch.

### Recommendation

Add a startup validation that asserts:
```rust
assert_eq!(
    bouncer_config.block_max_capacity.receipt_l2_gas,
    apollo_versioned_constants::VersionedConstants::latest_constants().max_block_size,
    "receipt_l2_gas must equal max_block_size"
);
```

Alternatively, derive `receipt_l2_gas` directly from `max_block_size` in the versioned constants rather than maintaining it as a separate config value, eliminating the possibility of divergence.

### Proof of Concept

1. Deploy a sequencer node with Starknet version 0.14.2 (`max_block_size = 5,800,000,000`, `gas_target = 1,500,000,000`).
2. Set `receipt_l2_gas = 4,000,000,000` in the batcher config (the value from 0.14.0, e.g., via the `replacer_batcher_config.json` template).
3. The bouncer closes blocks at 4,000,000,000 gas; the fee market computes price adjustments relative to `gas_target = 1,500,000,000`.
4. When blocks are full (4,000,000,000 gas used), the fee market sees `l2_gas_used / gas_target ≈ 2.67×`, computing a large price increase — but the actual block capacity is 4,000,000,000, not 5,800,000,000.
5. The resulting gas price diverges from the correct EIP-1559 value, producing an incorrect economic signal for all users submitting transactions.

Conversely, if `receipt_l2_gas = 5,800,000,000` but `max_block_size = 4,000,000,000` (old version), blocks fill up at 5,800,000,000 gas but the fee market computes prices relative to `gas_target = 3,200,000,000` (from 0.14.0), leading to underpriced gas.

### Citations

**File:** crates/apollo_versioned_constants/src/lib.rs (L17-20)
```rust
    /// The maximum block size in gas units.
    // NOTE: Must stay in sync with BouncerWeights receipt_l2_gas.
    // NOTE: When max_block_size is changed, update `gas_target` accordingly to maintain the ratio.
    pub max_block_size: GasAmount,
```

**File:** crates/blockifier/src/bouncer.rs (L163-168)
```rust
    /// Receipt-based L2 gas, including execution gas + state allocation costs + DA costs.
    /// Used to close blocks on the economic gas metric. Diverges from sierra_gas because
    /// it includes allocation_cost for new storage keys and other non-execution costs.
    // NOTE: Must stay in sync with orchestrator_versioned_constants' max_block_size.
    pub receipt_l2_gas: GasAmount,
}
```

**File:** crates/blockifier/src/bouncer.rs (L226-229)
```rust
            proving_gas: GasAmount(5000000000),
            // NOTE: Must stay in sync with orchestrator_versioned_constants' max_block_size.
            receipt_l2_gas: GasAmount(5800000000),
        }
```

**File:** crates/apollo_node/resources/config_schema.json (L102-106)
```json
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.receipt_l2_gas": {
    "description": "An upper bound on the total receipt-based L2 gas in a block. Includes execution gas plus state allocation costs. Should equal max_block_size.",
    "privacy": "Public",
    "value": 5800000000
  },
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L55-77)
```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return GasPrice(override_value);
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
}
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L86-101)
```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
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

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L326-333)
```rust
                let next_l2_gas_price = calculate_next_l2_gas_price_for_fin(
                    args.l2_gas_price,
                    args.build_param.height,
                    info.l2_gas_used,
                    args.override_l2_gas_price_fri,
                    &args.min_l2_gas_price_per_height,
                    args.fee_actual,
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

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_2.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 1500000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
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

**File:** crates/apollo_deployments/resources/app_configs/replacer_batcher_config.json (L12-12)
```json
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.receipt_l2_gas": "$$$_BATCHER_CONFIG-STATIC_CONFIG-BLOCK_BUILDER_CONFIG-BOUNCER_CONFIG-BLOCK_MAX_CAPACITY-RECEIPT_L2_GAS_$$$",
```
