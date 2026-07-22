### Title
Declare Transactions Bypass `max_l2_gas_amount` Cap in Stateless Gateway Admission - (`crates/apollo_gateway/src/stateless_transaction_validator.rs`)

---

### Summary

`StatelessTransactionValidator::validate_resource_bounds` enforces an upper bound on `l2_gas.max_amount` for `Invoke` and `DeployAccount` transactions but explicitly skips that check for `Declare` transactions. Any user can submit a `Declare` transaction with `l2_gas.max_amount` set to an arbitrarily large value (up to `u64::MAX`) and pass stateless gateway admission, violating the admission-control invariant that `max_l2_gas_amount` is supposed to enforce uniformly.

---

### Finding Description

In `validate_resource_bounds`, the cap check reads:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
    // ← empty branch: check is silently skipped
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
```

The production default for `max_l2_gas_amount` is `1_210_000_000` (confirmed in `StatelessTransactionValidatorConfig::default()` and `config_schema.json`). For every transaction type except `Declare`, submitting `l2_gas.max_amount > 1_210_000_000` causes an immediate `MaxGasAmountTooHigh` rejection. For `Declare`, the branch is empty and the check never executes.

The test `valid_l2_gas_amount_on_declare` in `stateless_transaction_validator_test.rs` explicitly documents and confirms this asymmetry: a `Declare` transaction with `l2_gas.max_amount = 200` passes even when `max_l2_gas_amount = 100`.

The `l2_gas.max_amount` field is part of the signed transaction hash and is used by the blockifier to derive the initial gas budget for execution (`user_initial_gas_from_bounds`). Accepting an unbounded value at the gateway layer breaks the admission invariant that is the direct analog of the FrankenDAO `maxStakeBonusTime` enforcement gap: a configured maximum exists, is enforced for all other types, but is silently not enforced for one type.

---

### Impact Explanation

**Impact: High — Mempool/gateway admission accepts invalid transactions before sequencing.**

A `Declare` transaction with `l2_gas.max_amount = u64::MAX` (18,446,744,073,709,551,615) passes the stateless validator unconditionally. The stateful validator's balance check provides a secondary defense only when `max_price_per_unit > 0` causes `max_possible_fee` to overflow into a value the account cannot cover. However:

1. The stateless validator is the canonical first-line admission gate; its invariant is broken for `Declare`.
2. The `l2_gas.max_amount` value is hashed into the transaction hash and signed by the account. A `Declare` transaction with an oversized bound that is later rejected by the stateful validator or blockifier has already consumed gateway resources and produced a signed hash that diverges from what the same transaction with a capped bound would produce.
3. If `validate_resource_bounds` is disabled in the stateful validator config (`validate_resource_bounds: false`), the secondary defense is also absent, and the oversized `Declare` reaches the mempool and blockifier with no cap on its declared gas budget.

---

### Likelihood Explanation

**Likelihood: High.**

The trigger requires no privilege: any user can construct and submit a `Declare` transaction. The gap is unconditional — the empty `if let RpcTransaction::Declare(_) = tx {}` branch always skips the check regardless of any other field values. The TODO comment confirms the gap is known but unresolved.

---

### Recommendation

Remove the asymmetric branch and apply the same `max_l2_gas_amount` check to `Declare` transactions:

```rust
fn validate_resource_bounds(
    &self,
    tx: &RpcTransaction,
) -> StatelessTransactionValidatorResult<()> {
    if !self.config.validate_resource_bounds {
        return Ok(());
    }

    let resource_bounds = *tx.resource_bounds();
    if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
        return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
    }

    if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
        return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { … });
    }

    // Apply to ALL transaction types, including Declare.
    if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
        return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
    }

    Ok(())
}
```

---

### Proof of Concept

1. Construct a valid `RpcDeclareTransaction::V3` with:
   - `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)`
   - `resource_bounds.l2_gas.max_price_per_unit = GasPrice(min_gas_price)` (to pass the price floor check)
2. Call `StatelessTransactionValidator::validate(&tx)` with `validate_resource_bounds: true` and `max_l2_gas_amount: 1_210_000_000`.
3. Observe: the call returns `Ok(())` — the oversized `max_amount` is accepted.
4. Repeat with `RpcInvokeTransaction::V3` using the same bounds.
5. Observe: the call returns `Err(MaxGasAmountTooHigh { gas_amount: u64::MAX, max_gas_amount: 1_210_000_000 })`.

The existing test `valid_l2_gas_amount_on_declare` in `crates/apollo_gateway/src/stateless_transaction_validator_test.rs` already encodes this behavior as an expected pass, confirming the gap is present and untested as a failure case. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L78-85)
```rust
        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L173-201)
```rust
#[rstest]
#[case::l2_gas_amount_out_of_limit(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(200),
                ..NON_EMPTY_RESOURCE_BOUNDS
            },
            ..Default::default()
        },
        ..Default::default()
    }
)]
fn valid_l2_gas_amount_on_declare(
    #[case] config: StatelessTransactionValidatorConfig,
    #[case] rpc_tx_args: RpcTransactionArgs,
) {
    let tx_type = TransactionType::Declare;
    let tx_validator = StatelessTransactionValidator { config };

    let tx = rpc_tx_for_testing(tx_type, rpc_tx_args);

    assert_matches!(tx_validator.validate(&tx), Ok(()));
}
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-204)
```rust
impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_210_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 9, usize::MAX),
            allow_client_side_proving: true,
            max_proof_size: 480000,
        }
    }
}
```

**File:** crates/apollo_node/resources/config_schema.json (L3172-3176)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": {
    "description": "Maximum allowed L2 gas amount for transactions.",
    "privacy": "Public",
    "value": 1210000000
  },
```
