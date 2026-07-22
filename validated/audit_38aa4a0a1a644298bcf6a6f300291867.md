### Title
Non-Atomic `BouncerWeights::receipt_l2_gas` / `VersionedConstants::max_block_size` Divergence Corrupts EIP-1559 Fee Calculation - (File: `crates/blockifier/src/bouncer.rs`, `crates/apollo_versioned_constants/src/lib.rs`)

### Summary

`BouncerWeights::receipt_l2_gas` (the block-level gas cap enforced by the blockifier bouncer) and `VersionedConstants::max_block_size` (the EIP-1559 fee-market reference ceiling) are explicitly documented as needing to be equal, but they live in two completely separate configuration systems with no runtime validation enforcing the invariant. When they diverge, the EIP-1559 next-block gas price is computed against the wrong ceiling, producing a systematically wrong `next_l2_gas_price` that is committed to every subsequent block header.

### Finding Description

**The invariant and its documentation**

`BouncerWeights::receipt_l2_gas` is the field that closes a block when the accumulated receipt-based L2 gas reaches the configured limit. Its doc comment and default value both carry an explicit cross-component note:

```
// NOTE: Must stay in sync with orchestrator_versioned_constants' max_block_size.
receipt_l2_gas: GasAmount(5800000000),
```

The `config_schema.json` description for the same field reads: *"Should equal max_block_size."*

`VersionedConstants::max_block_size` is the ceiling used inside `calculate_next_base_gas_price` to validate the gas-target and to bound the EIP-1559 price-change formula:

```rust
assert!(gas_target < versioned_constants.max_block_size, "Gas target must be lower than max block size.");
let denominator = gas_target_u256 * U256::from(versioned_constants.gas_price_max_change_denominator);
let price_change = (price_u256 * gas_delta) / denominator;
```

**The two independent configuration paths**

| Parameter | Config system | Location |
|---|---|---|
| `receipt_l2_gas` | `BatcherConfig` → `BouncerConfig` → `BouncerWeights` | node config file / `config_schema.json` |
| `max_block_size` | `apollo_versioned_constants::VersionedConstants` | per-version JSON files (`orchestrator_versioned_constants_*.json`) |

`receipt_l2_gas` is loaded from the operator's node config. `max_block_size` is baked into the versioned-constants JSON that is selected by the current Starknet protocol version. They are never compared at startup, at config validation time, or at block-building time.

**Concrete divergence across existing versions**

| Version | `max_block_size` | Default `receipt_l2_gas` | Delta |
|---|---|---|---|
| V0_14_0 | 4 000 000 000 | 5 800 000 000 | +1 800 000 000 |
| V0_14_1 | 5 000 000 000 | 5 800 000 000 | +800 000 000 |
| V0_14_2–4 | 5 800 000 000 | 5 800 000 000 | 0 (correct) |

A node running V0_14_0 or V0_14_1 versioned constants with the default bouncer config (or any node where an operator updates one value but not the other) is immediately in a diverged state.

**Effect on the fee market**

`calculate_next_l2_gas_price_for_fin` feeds the actual `l2_gas_consumed` of the just-closed block as `gas_used` into `calculate_next_base_gas_price`. The bouncer closes the block when accumulated `receipt_l2_gas` reaches `block_max_capacity.receipt_l2_gas`. The fee-market formula then computes:

```
price_change = price × |gas_used − gas_target| / (gas_target × denominator)
```

where `gas_target` is taken from `VersionedConstants` (e.g. 1 040 000 000 for V0_14_3). If `receipt_l2_gas` > `max_block_size`, a full block carries `gas_used` up to 5 800 000 000 while the formula's denominator is anchored to a `max_block_size` of 4 000 000 000. The price-change numerator is inflated by the ratio `receipt_l2_gas / max_block_size`, causing the next-block gas price to increase faster than the protocol intends. This wrong price is written into the block header as `next_l2_gas_price` and propagated to every subsequent block.

Conversely, if `receipt_l2_gas` < `max_block_size`, blocks close early, `gas_used` never reaches the fee-market's reference ceiling, and prices never rise as intended—suppressing fees below the correct EIP-1559 equilibrium.

### Impact Explanation

Every block header's `next_l2_gas_price` field is computed from the wrong ceiling. All fee estimation, RPC `starknet_estimateFee`, and actual transaction fee deductions downstream of that price are wrong. This is a persistent, systematic economic error: **incorrect fee/gas accounting with economic impact** (Critical scope per the allowed impact list). The wrong price is also the input to the next block's fee calculation, so the error compounds over time.

### Likelihood Explanation

The versioned constants are updated with every Starknet protocol version bump (five versions already exist: V0_14_0 through V0_14_4, each with a different `max_block_size`). The bouncer config is updated separately via the node config file. Any operator who upgrades the protocol version without simultaneously updating `receipt_l2_gas`—or who uses the compiled-in `BouncerWeights::default()` while running an older versioned-constants file—silently enters the diverged state. No startup check, no `validate_node_config` cross-member rule, and no assertion inside the block-building path catches the mismatch.

### Recommendation

1. Add a startup cross-member validation (alongside the existing checks in `cross_member_validations`) that asserts `bouncer_config.block_max_capacity.receipt_l2_gas == VersionedConstants::latest_constants().max_block_size` and returns a hard `ConfigError` if they differ.
2. Alternatively, remove `receipt_l2_gas` as an independent config field and derive it directly from `VersionedConstants::max_block_size` at `BouncerConfig` construction time, eliminating the possibility of divergence entirely.

### Proof of Concept

1. Start a node with `orchestrator_versioned_constants_0_14_0.json` selected (`max_block_size = 4_000_000_000`) while keeping the default `receipt_l2_gas = 5_800_000_000` in the batcher config.
2. The node starts without error—no validation fires.
3. Build a block that fills to `receipt_l2_gas = 5_800_000_000`. The bouncer accepts it.
4. `calculate_next_l2_gas_price_for_fin` is called with `l2_gas_used = 5_800_000_000`, `gas_target = 3_200_000_000` (V0_14_0 value), `max_block_size = 4_000_000_000`.
5. `gas_delta = |5_800_000_000 − 3_200_000_000| = 2_600_000_000`; correct delta for a full block would be `|4_000_000_000 − 3_200_000_000| = 800_000_000`.
6. The computed `price_change` is inflated by factor `2_600_000_000 / 800_000_000 = 3.25×`, producing a `next_l2_gas_price` that is 3.25× higher than the protocol-correct value and is committed to the block header. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** crates/blockifier/src/bouncer.rs (L163-168)
```rust
    /// Receipt-based L2 gas, including execution gas + state allocation costs + DA costs.
    /// Used to close blocks on the economic gas metric. Diverges from sierra_gas because
    /// it includes allocation_cost for new storage keys and other non-execution costs.
    // NOTE: Must stay in sync with orchestrator_versioned_constants' max_block_size.
    pub receipt_l2_gas: GasAmount,
}
```

**File:** crates/blockifier/src/bouncer.rs (L216-230)
```rust
impl Default for BouncerWeights {
    // TODO(Yael): update the default values once the actual values are known.
    fn default() -> Self {
        Self {
            l1_gas: 2500000,
            message_segment_length: 3700,
            n_events: 5000,
            n_txs: 600,
            state_diff_size: 4000,
            sierra_gas: GasAmount(5000000000),
            proving_gas: GasAmount(5000000000),
            // NOTE: Must stay in sync with orchestrator_versioned_constants' max_block_size.
            receipt_l2_gas: GasAmount(5800000000),
        }
    }
```

**File:** crates/blockifier/src/bouncer.rs (L277-283)
```rust
        dump.append(&mut BTreeMap::from([ser_param(
            "receipt_l2_gas",
            &self.receipt_l2_gas,
            "An upper bound on the total receipt-based L2 gas in a block. Includes execution gas \
             plus state allocation costs. Should equal max_block_size.",
            ParamPrivacyInput::Public,
        )]));
```

**File:** crates/apollo_versioned_constants/src/lib.rs (L17-23)
```rust
    /// The maximum block size in gas units.
    // NOTE: Must stay in sync with BouncerWeights receipt_l2_gas.
    // NOTE: When max_block_size is changed, update `gas_target` accordingly to maintain the ratio.
    pub max_block_size: GasAmount,
    /// The target gas usage per block. Used by EIP-1559 to calculate the next block's gas price.
    // Target is 60% of max_block_size, making price adjustment more responsive to congestion.
    pub gas_target: GasAmount,
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

**File:** crates/apollo_node/resources/config_schema.json (L102-106)
```json
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.receipt_l2_gas": {
    "description": "An upper bound on the total receipt-based L2 gas in a block. Includes execution gas plus state allocation costs. Should equal max_block_size.",
    "privacy": "Public",
    "value": 5800000000
  },
```

**File:** crates/apollo_node_config/src/node_config.rs (L485-511)
```rust
    pub fn validate_node_config(&self) -> Result<(), ConfigError> {
        // Validate each config member using its `Validate` trait derivation.
        config_validate(self)?;

        // Custom cross member validations.
        self.cross_member_validations()
    }

    fn cross_member_validations(&self) -> Result<(), ConfigError> {
        macro_rules! validate_component_config_is_set_iff_running_locally {
            ($component_field:ident, $config_field:ident) => {{
                // The component config should be set iff its running locally.
                if self.components.$component_field.is_running_locally()
                    != self.$config_field.is_some()
                {
                    let execution_mode = &self.components.$component_field.execution_mode;
                    let component_config_availability =
                        if self.$config_field.is_some() { "available" } else { "not available" };
                    return Err(ConfigError::ComponentConfigMismatch {
                        component_config_mismatch: format!(
                            "{} component configs mismatch: execution mode {:?} while config is {}",
                            stringify!($component_field),
                            execution_mode,
                            component_config_availability
                        ),
                    });
                }
```

**File:** crates/apollo_batcher_config/src/config.rs (L436-445)
```rust
fn validate_batcher_config(batcher_config: &BatcherConfig) -> Result<(), ValidationError> {
    if batcher_config.static_config.input_stream_content_buffer_size
        < batcher_config.dynamic_config.n_concurrent_txs
    {
        return Err(ValidationError::new(
            "input_stream_content_buffer_size must be at least n_concurrent_txs",
        ));
    }
    Ok(())
}
```
