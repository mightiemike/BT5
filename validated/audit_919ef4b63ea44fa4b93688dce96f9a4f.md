### Title
Gateway Resource Bounds Validation Uses Stale Previous-Block L2 Gas Price Instead of `next_l2_gas_price` — (`crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

`StatefulTransactionValidator::validate_resource_bounds` validates a transaction's `max_price_per_unit` for L2 gas against the **current committed block's** `l2_gas_price`, not the `next_l2_gas_price` that will actually govern execution of the incoming transaction. When the EIP-1559 mechanism raises the L2 gas price between blocks, the gateway admits transactions whose L2 gas price bound is below the price of the block they will execute in, causing them to fail at blockifier pre-validation. The code itself carries an explicit `TODO` acknowledging the wrong field is being read.

---

### Finding Description

`validate_resource_bounds` reads the L2 gas price from `get_block_info()`, which returns the gas prices **of the already-committed block**:

```rust
// crates/apollo_gateway/src/stateful_transaction_validator.rs, lines 229-240
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
``` [1](#0-0) 

The threshold computed inside `validate_tx_l2_gas_price_within_threshold` is:

```
threshold = (min_gas_price_percentage / 100) × previous_block.l2_gas_price
``` [2](#0-1) 

The block header already carries a `next_l2_gas_price` field — the EIP-1559-adjusted price for the **next** block — which is what the transaction will actually be executed against:

```rust
// protobuf converter, line 175-178
let next_l2_gas_price = u128::from(
    value.next_l2_gas_price.ok_or(missing("SignedBlockHeader::next_l2_gas_price"))?,
).into();
``` [3](#0-2) 

The EIP-1559 formula that produces `next_l2_gas_price` can raise the price by up to `1/gas_price_max_change_denominator` per block when the block is congested:

```rust
// crates/apollo_consensus_orchestrator/src/fee_market/mod.rs, lines 124-129
let denominator =
    gas_target_u256 * U256::from(versioned_constants.gas_price_max_change_denominator);
let price_change = (price_u256 * gas_delta) / denominator;
let adjusted_price_u256 =
    if gas_used > gas_target { price_u256 + price_change } else { price_u256 - price_change };
``` [4](#0-3) 

When the batcher executes the transaction, the blockifier's pre-validation stage checks the transaction's `max_price_per_unit` against the **actual** block's L2 gas price:

```rust
// crates/blockifier/src/transaction/account_transaction.rs, lines 441-448
if resource_bounds.max_price_per_unit < actual_gas_price.get() {
    insufficiencies_resource.push(
        ResourceBoundsError::MaxGasPriceTooLow { ... }
    );
}
``` [5](#0-4) 

The gateway and the blockifier therefore evaluate the same transaction against **different L2 gas prices**: the gateway uses the stale committed-block price; the blockifier uses the live next-block price.

---

### Impact Explanation

**High — Mempool/gateway admission accepts invalid transactions before sequencing.**

When the L2 gas price is rising (congested blocks), the gateway's threshold is lower than the price the transaction will face at execution. A transaction with:

```
(min_gas_price_percentage/100) × P_N  ≤  tx.max_price_per_unit  <  P_{N+1}
```

passes gateway admission but is rejected by the blockifier with `MaxGasPriceTooLow` during pre-validation. The transaction consumes mempool resources, occupies a batcher slot, and wastes sequencer work — all while being provably invalid for the block it will execute in. The discrepancy grows with block congestion and is bounded only by `gas_price_max_change_denominator`.

---

### Likelihood Explanation

The condition is triggered whenever consecutive blocks are congested enough to raise `next_l2_gas_price` above `previous_block.l2_gas_price` by more than the `min_gas_price_percentage` slack. No privileged access is required; any user submitting a transaction with `max_price_per_unit` set to exactly the gateway threshold (a natural choice for fee-minimizing wallets) will hit this. The TODO comment in the source confirms the developers are aware the wrong field is being read.

---

### Recommendation

Replace the `l2_gas_price` read from `BlockInfo` with the `next_l2_gas_price` field from the block header, as the TODO comment already prescribes:

```rust
// Instead of:
let previous_block_l2_gas_price = self
    .gateway_fixed_block_state_reader
    .get_block_info()
    .await?
    .gas_prices
    .strk_gas_prices
    .l2_gas_price;

// Use:
let next_l2_gas_price = self
    .gateway_fixed_block_state_reader
    .get_next_l2_gas_price()   // read next_l2_gas_price from block header
    .await?;
```

`GatewayFixedBlockStateReader` should expose `next_l2_gas_price` from `BlockHeaderWithoutHash` so the gateway validates against the price the transaction will actually face.

---

### Proof of Concept

1. Block N executes at 90% of gas target → EIP-1559 raises `next_l2_gas_price` to `P_{N+1} = P_N × (1 + delta)` where `delta > 0`.
2. `next_l2_gas_price = P_{N+1}` is stored in block N's header.
3. Attacker submits an invoke transaction with `l2_gas.max_price_per_unit = P_N` (exactly the previous block's price, which satisfies `min_gas_price_percentage = 100%`).
4. `validate_tx_l2_gas_price_within_threshold` computes `threshold = 1.0 × P_N = P_N`; check passes; transaction is admitted to the mempool.
5. Batcher builds block N+1 with `l2_gas_price = P_{N+1} > P_N`.
6. Blockifier pre-validation: `tx.max_price_per_unit (P_N) < actual_gas_price (P_{N+1})` → `MaxGasPriceTooLow` error.
7. Transaction is reverted/dropped after consuming sequencer resources, despite having passed gateway admission.

The divergent values are: gateway threshold = `P_N`; blockifier enforcement price = `P_{N+1}`. The wrong field read is `block_info.gas_prices.strk_gas_prices.l2_gas_price` at line 235 of `crates/apollo_gateway/src/stateful_transaction_validator.rs` instead of `block_header_without_hash.next_l2_gas_price`. [6](#0-5)

### Citations

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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L358-390)
```rust
    // TODO(Arni): Consider running this validation for all gas prices.
    fn validate_tx_l2_gas_price_within_threshold(
        &self,
        tx_resource_bounds: ValidResourceBounds,
        previous_block_l2_gas_price: NonzeroGasPrice,
    ) -> StatefulTransactionValidatorResult<()> {
        match tx_resource_bounds {
            ValidResourceBounds::AllResources(tx_resource_bounds) => {
                let tx_l2_gas_price = tx_resource_bounds.l2_gas.max_price_per_unit;
                let gas_price_threshold_multiplier =
                    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
                let threshold = (gas_price_threshold_multiplier
                    * previous_block_l2_gas_price.get().0)
                    .to_integer();
                if tx_l2_gas_price.0 < threshold {
                    return Err(StarknetError {
                        // We didn't have this kind of an error.
                        code: StarknetErrorCode::UnknownErrorCode(
                            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
                        ),
                        message: format!(
                            "Transaction L2 gas price {tx_l2_gas_price} is below the required \
                             threshold {threshold}.",
                        ),
                    });
                }
            }
            ValidResourceBounds::L1Gas(_) => {
                // No validation required for legacy transactions.
            }
        }
        Ok(())
    }
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L175-178)
```rust
        let next_l2_gas_price = u128::from(
            value.next_l2_gas_price.ok_or(missing("SignedBlockHeader::next_l2_gas_price"))?,
        )
        .into();
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L124-129)
```rust
    let denominator =
        gas_target_u256 * U256::from(versioned_constants.gas_price_max_change_denominator);
    let price_change = (price_u256 * gas_delta) / denominator;

    let adjusted_price_u256 =
        if gas_used > gas_target { price_u256 + price_change } else { price_u256 - price_change };
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L441-448)
```rust
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
```
