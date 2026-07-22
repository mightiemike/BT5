### Title
Static `min_gas_price` in `StatelessTransactionValidatorConfig` Not Synchronized with `VersionedConstants.min_gas_price`, Causing Gateway Admission to Diverge from Blockifier Execution Criteria — (`File: crates/apollo_gateway_config/src/config.rs`)

---

### Summary

The gateway's stateless admission layer enforces a static, operator-configured `min_gas_price` that is completely independent of the protocol-level `VersionedConstants.min_gas_price`. When the Starknet protocol upgrades and the versioned constant changes, the two values diverge. Depending on the direction of the change, the gateway either accepts transactions the blockifier will reject, or rejects transactions the blockifier would accept. The codebase itself acknowledges this as an unresolved design flaw via a TODO comment at the exact site of the divergence.

---

### Finding Description

`StatelessTransactionValidatorConfig` carries its own `min_gas_price: u128` field, hardcoded to `8_000_000_000` in the default implementation:

```rust
// TODO(AlonH): Remove the `min_gas_price` field from this struct and use the one from the
// versioned constants.
pub min_gas_price: u128,
``` [1](#0-0) 

The default value is:

```rust
min_gas_price: 8_000_000_000,
``` [2](#0-1) 

This value is checked in `StatelessTransactionValidator::validate_resource_bounds`:

```rust
if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
    return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { ... });
}
``` [3](#0-2) 

The protocol-level minimum gas price lives in a completely separate struct, `apollo_versioned_constants::VersionedConstants`, which is keyed to the Starknet version and changes across upgrades:

```rust
pub struct VersionedConstants {
    pub min_gas_price: GasPrice,
    ...
}
define_versioned_constants!(
    VersionedConstants,
    VersionedConstantsError,
    StarknetVersion::V0_14_0,
    ...
    (V0_14_4, "../resources/orchestrator_versioned_constants_0_14_4.json"),
);
``` [4](#0-3) 

The orchestrator's EIP-1559 fee market uses `VersionedConstants::latest_constants().min_gas_price` to compute the next block's base gas price:

```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
    let versioned_constants = VersionedConstants::latest_constants();
``` [5](#0-4) 

The blockifier's pre-validation then checks the transaction's `max_price_per_unit` against the **actual block gas price** (which is derived from the versioned constant minimum via EIP-1559):

```rust
if resource_bounds.max_price_per_unit < actual_gas_price.get() {
    insufficiencies_resource.push(
        ResourceBoundsError::MaxGasPriceTooLow { ... }
    );
}
``` [6](#0-5) 

The two values — `StatelessTransactionValidatorConfig.min_gas_price` (static, operator-set) and the actual block gas price (dynamic, derived from `VersionedConstants.min_gas_price`) — are never reconciled. There is no code path that reads `VersionedConstants.min_gas_price` and feeds it into the stateless validator.

---

### Impact Explanation

**Scenario A — Gateway too permissive (invalid transactions accepted):**
If `VersionedConstants.min_gas_price` increases across a Starknet version upgrade (e.g., from 8 Gwei to 12 Gwei) but the static gateway config is not updated, the stateless validator continues to accept transactions with `max_price_per_unit` between 8 Gwei and 12 Gwei. These transactions pass stateless admission, enter the mempool, and are later rejected by the blockifier's pre-validation with `MaxGasPriceTooLow`. This matches the allowed impact: **"Mempool/gateway/RPC admission accepts invalid transactions … before sequencing."**

**Scenario B — Gateway too restrictive (valid transactions rejected):**
If `VersionedConstants.min_gas_price` decreases (e.g., from 8 Gwei to 4 Gwei) but the static gateway config remains at 8 Gwei, transactions with `max_price_per_unit` between 4 Gwei and 8 Gwei are rejected at the gateway even though the blockifier would accept and execute them. This matches: **"Mempool/gateway/RPC admission … rejects valid transactions before sequencing."**

The stateful validator's `validate_tx_l2_gas_price_within_threshold` provides a partial second check against `previous_block_l2_gas_price`, but it is gated by `validate_resource_bounds: bool` (which can be disabled during bootstrap) and `min_gas_price_percentage: u8` (which can be set to 0). The stateless validator is the unconditional first gate and is the one with the static divergent value. [7](#0-6) 

---

### Likelihood Explanation

**High.** The Starknet protocol is actively upgraded across versioned constants files (V0_13_0 through V0_14_4 are already present). Each upgrade can change `min_gas_price`. The static gateway config is a separate deployment artifact that operators must manually keep in sync. The codebase itself documents this as an unresolved divergence with a `TODO` comment at the exact site. No automated mechanism exists to synchronize the two values. Under EIP-1559 dynamics, the actual block gas price fluctuates continuously, making the static value stale by design.

---

### Recommendation

Replace `StatelessTransactionValidatorConfig.min_gas_price` with a read from `VersionedConstants::latest_constants().min_gas_price` (or the version-appropriate constant for the current block), as the TODO comment already prescribes. The stateless validator should derive its floor from the same source as the blockifier and the fee market, eliminating the static divergence. [1](#0-0) 

---

### Proof of Concept

1. Starknet upgrades to a new version. The new `orchestrator_versioned_constants_X.json` sets `min_gas_price` to `12_000_000_000` (12 Gwei).
2. The gateway deployment config (`gateway_config.json`) retains `stateless_tx_validator_config.min_gas_price: 8000000000`. [8](#0-7) 

3. A user submits an invoke transaction with `resource_bounds.l2_gas.max_price_per_unit = 10_000_000_000` (10 Gwei).
4. `StatelessTransactionValidator::validate_resource_bounds` evaluates `10_000_000_000 >= 8_000_000_000` → **PASS**. Transaction is admitted to the mempool. [3](#0-2) 

5. The batcher builds a block. The EIP-1559 fee market, using `VersionedConstants::latest_constants().min_gas_price = 12 Gwei`, has set the block's `l2_gas_price` to 12 Gwei.
6. The blockifier's `check_fee_bounds` evaluates `10_000_000_000 < 12_000_000_000` → **FAIL** with `ResourceBoundsError::MaxGasPriceTooLow`. [6](#0-5) 

The transaction was admitted by the gateway under the old static threshold but is rejected at execution time under the new versioned threshold — the exact config-boundary divergence described in the seed report.

### Citations

**File:** crates/apollo_gateway_config/src/config.rs (L170-172)
```rust
    // TODO(AlonH): Remove the `min_gas_price` field from this struct and use the one from the
    // versioned constants.
    pub min_gas_price: u128,
```

**File:** crates/apollo_gateway_config/src/config.rs (L192-192)
```rust
            min_gas_price: 8_000_000_000,
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L71-75)
```rust
        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
```

**File:** crates/apollo_versioned_constants/src/lib.rs (L10-43)
```rust
pub struct VersionedConstants {
    ///  This is used to calculate the base gas price for the next block according to EIP-1559 and
    /// serves as a sensitivity parameter that limits the maximum rate of change of the gas price
    /// between consecutive blocks.
    pub gas_price_max_change_denominator: u128,
    /// The minimum gas price in fri.
    pub min_gas_price: GasPrice,
    /// The maximum block size in gas units.
    // NOTE: Must stay in sync with BouncerWeights receipt_l2_gas.
    // NOTE: When max_block_size is changed, update `gas_target` accordingly to maintain the ratio.
    pub max_block_size: GasAmount,
    /// The target gas usage per block. Used by EIP-1559 to calculate the next block's gas price.
    // Target is 60% of max_block_size, making price adjustment more responsive to congestion.
    pub gas_target: GasAmount,
    /// The margin for the eth to fri rate disagreement, expressed as a percentage (parts per
    /// hundred).
    pub l1_gas_price_margin_percent: u32,
    /// Number of `fee_proposal` values used to compute `fee_actual` (sliding window).
    pub fee_proposal_window_size: u64,
    /// Maximum `fee_proposal` change per block in parts per thousand (e.g., `2` = 0.2%).
    pub fee_proposal_margin_ppt: u128,
}

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

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L86-92)
```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
    let versioned_constants = VersionedConstants::latest_constants();
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L441-449)
```rust
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
                            }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-243)
```rust
    async fn validate_resource_bounds(
        &self,
        executable_tx: &ExecutableTransaction,
    ) -> StatefulTransactionValidatorResult<()> {
        // Skip this validation during the systems bootstrap phase.
        if self.config.validate_resource_bounds {
            // TODO(Arni): getnext_l2_gas_price from the block header.
            let previous_block_l2_gas_price = self
                .gateway_fixed_block_state_reader
                .get_block_info()
                .await?
                .gas_prices
                .strk_gas_prices
                .l2_gas_price;
            self.validate_tx_l2_gas_price_within_threshold(
                executable_tx.resource_bounds(),
                previous_block_l2_gas_price,
            )?;
        }
        Ok(())
    }
```

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L31-31)
```json
  "gateway_config.static_config.stateless_tx_validator_config.min_gas_price": 8000000000,
```
