### Title
Gateway `validate_resource_bounds` Unconditionally Rejects Valid Client-Side Proving Transactions With Legitimately Zero Gas Prices - (`crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

`StatelessTransactionValidator::validate_resource_bounds` applies two zero-price checks to every incoming transaction before it ever inspects whether the transaction is a client-side proving transaction. The prover protocol mandates that all `max_price_per_unit` fields be zero. The gateway's checks mandate the opposite. Both flags are enabled by default, so every valid proving invoke transaction is rejected at the gateway admission boundary.

### Finding Description

`StatelessTransactionValidator::validate` calls `validate_resource_bounds` unconditionally before it reaches `validate_client_side_proving_allowed`:

```
validate_resource_bounds(tx)?;          // line 40 — runs first
...
validate_client_side_proving_allowed(invoke_tx)?;  // line 46 — never reached for proving txs
```

Inside `validate_resource_bounds` there are two blocking checks:

**Check 1 — ZeroResourceBounds** (line 66–68):
```rust
if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
    return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
}
```

**Check 2 — MaxGasPriceTooLow** (line 71–75):
```rust
if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
    return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { ... });
}
```

The production default config (in `StatelessTransactionValidatorConfig::default()`) sets:
- `validate_resource_bounds: true`
- `min_gas_price: 8_000_000_000`
- `allow_client_side_proving: true`

The prover's own admission function `validate_zero_fee_resource_bounds` (in `virtual_snos_prover.rs`) **requires** that a valid proving transaction have all `max_price_per_unit` fields equal to zero and `l2_gas.max_amount` non-zero:

```rust
if bounds.l1_gas.max_price_per_unit != GasPrice(0) { violations.push(...) }
if bounds.l2_gas.max_price_per_unit != GasPrice(0) { violations.push(...) }
if bounds.l1_data_gas.max_price_per_unit != GasPrice(0) { violations.push(...) }
```

A conforming proving transaction therefore has:
- `l1_gas = {max_amount: any, max_price_per_unit: 0}`
- `l2_gas = {max_amount: N > 0, max_price_per_unit: 0}`
- `l1_data_gas = {max_amount: any, max_price_per_unit: 0}`

`max_possible_fee(Tip::ZERO)` for this transaction:
```
= l1_gas.max_amount * 0 + l2_gas.max_amount * (0 + 0) + l1_data_gas.max_amount * 0 = 0
```

→ **Check 1 fires**: `ZeroResourceBounds` error.

Even if Check 1 were removed, `l2_gas.max_price_per_unit = 0 < 8_000_000_000 = min_gas_price`:

→ **Check 2 fires**: `MaxGasPriceTooLow` error.

The existing test `test_positive_flow` with `#[case::client_side_proving]` only passes because it uses `DEFAULT_VALIDATOR_CONFIG_FOR_TESTING` which explicitly sets `validate_resource_bounds: false` and `min_gas_price: 0` — masking the conflict entirely. The production config has both flags set to values that block proving transactions.

### Impact Explanation

Every valid client-side proving invoke transaction submitted to the gateway is rejected at stateless validation with either `ZeroResourceBounds` or `MaxGasPriceTooLow` before the transaction type is even inspected. This is a gateway admission defect: valid transactions are rejected before sequencing. The `allow_client_side_proving: true` production default is rendered inoperative.

### Likelihood Explanation

The conflict is structural and deterministic. Any user following the prover's documented requirement (zero prices) and submitting through the gateway will be rejected 100% of the time when `validate_resource_bounds: true` and `min_gas_price > 0`, both of which are production defaults. No special attacker capability is required — any ordinary user attempting to use client-side proving triggers the rejection.

### Recommendation

`validate_resource_bounds` must detect proving transactions before applying fee-positive checks. The simplest fix is to skip the zero-fee and min-price checks when the transaction carries proof data (i.e., when it is a client-side proving transaction):

```rust
fn validate_resource_bounds(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
    if !self.config.validate_resource_bounds {
        return Ok(());
    }

    // Client-side proving transactions legitimately carry zero prices.
    if let RpcTransaction::Invoke(RpcInvokeTransaction::V3(invoke_tx)) = tx {
        if !invoke_tx.proof_facts.is_empty() || !invoke_tx.proof.is_empty() {
            // Skip fee-positive checks; zero prices are required by the prover protocol.
            return Ok(());
        }
    }

    let resource_bounds = *tx.resource_bounds();
    if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
        return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
    }
    if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
        return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { ... });
    }
    ...
}
```

Alternatively, reorder the validation pipeline so `validate_client_side_proving_allowed` runs before `validate_resource_bounds`, and pass the proving-transaction flag into the resource bounds check.

### Proof of Concept

Using the production default `StatelessTransactionValidatorConfig` (both `validate_resource_bounds: true` and `min_gas_price: 8_000_000_000` and `allow_client_side_proving: true`):

```rust
let config = StatelessTransactionValidatorConfig::default();
// config.validate_resource_bounds == true
// config.min_gas_price == 8_000_000_000
// config.allow_client_side_proving == true

let validator = StatelessTransactionValidator { config };

// Construct a proving transaction with zero prices (as required by validate_zero_fee_resource_bounds)
let proving_tx = rpc_invoke_tx(invoke_tx_args!(
    resource_bounds: ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas: ResourceBounds { max_amount: GasAmount(0), max_price_per_unit: GasPrice(0) },
        l2_gas: ResourceBounds { max_amount: GasAmount(100_000_000), max_price_per_unit: GasPrice(0) },
        l1_data_gas: ResourceBounds { max_amount: GasAmount(0), max_price_per_unit: GasPrice(0) },
    }),
    proof_facts: create_valid_proof_facts_for_testing(),
    proof: Proof::proof_for_testing(),
));

// Expected: Ok(()) because allow_client_side_proving is true and prices are legitimately zero.
// Actual:   Err(ZeroResourceBounds { ... }) — rejected before the proving check is reached.
assert_matches!(validator.validate(&proving_tx), Ok(()));
```

The test fails with `ZeroResourceBounds` because `validate_resource_bounds` (line 40) fires before `validate_client_side_proving_allowed` (line 46). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L33-54)
```rust
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
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

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L392-446)
```rust
/// Validates resource bounds for proving, collecting all violations into a single error.
///
/// Since proving is client-side, no fees are charged. All `max_price_per_unit` fields and `tip`
/// must be zero. The `max_amount` fields have different semantics:
/// - `l2_gas.max_amount`: determines the gas limit the OS enforces on the transaction. Must be
///   non-zero. Set this to the value returned by `starknet_estimateFee`, or use a safe upper bound
///   like 100,000,000 (sufficient for ~1 million Cairo steps).
/// - `l1_gas.max_amount` and `l1_data_gas.max_amount`: do not affect OS execution and can be any
///   value.
fn validate_zero_fee_resource_bounds(
    tx: &RpcInvokeTransactionV3,
) -> Result<(), VirtualSnosProverError> {
    let bounds = &tx.resource_bounds;
    let mut violations = Vec::new();

    if bounds.l1_gas.max_price_per_unit != GasPrice(0) {
        violations
            .push(format!("l1_gas.max_price_per_unit = {}", bounds.l1_gas.max_price_per_unit.0));
    }
    if bounds.l2_gas.max_price_per_unit != GasPrice(0) {
        violations
            .push(format!("l2_gas.max_price_per_unit = {}", bounds.l2_gas.max_price_per_unit.0));
    }
    if bounds.l1_data_gas.max_price_per_unit != GasPrice(0) {
        violations.push(format!(
            "l1_data_gas.max_price_per_unit = {}",
            bounds.l1_data_gas.max_price_per_unit.0
        ));
    }
    if tx.tip != Tip(0) {
        violations.push(format!("tip = {}", tx.tip.0));
    }

    if !violations.is_empty() {
        return Err(VirtualSnosProverError::InvalidTransactionInput(format!(
            "Proving is client-side — no fees are charged. The following fields must be zero but \
             were not: [{}]. Set all max_price_per_unit fields and tip to 0x0. Note: max_amount \
             fields are fine to set — l2_gas.max_amount controls the gas limit enforced by the OS \
             (use the value from starknet_estimateFee, or 100000000 as a safe upper bound). \
             l1_gas.max_amount and l1_data_gas.max_amount do not affect OS execution.",
            violations.join(", ")
        )));
    }

    if bounds.l2_gas.max_amount == GasAmount(0) {
        return Err(VirtualSnosProverError::InvalidTransactionInput(
            "l2_gas.max_amount must be non-zero — it is the gas limit enforced by the OS on the \
             transaction. Set this to the value returned by starknet_estimateFee, or use \
             100000000 (0x5f5e100) as a safe upper bound (sufficient for ~1 million Cairo steps)."
                .to_string(),
        ));
    }

    Ok(())
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L54-67)
```rust
static DEFAULT_VALIDATOR_CONFIG_FOR_TESTING: LazyLock<StatelessTransactionValidatorConfig> =
    LazyLock::new(|| StatelessTransactionValidatorConfig {
        validate_resource_bounds: false,
        min_gas_price: 0,
        max_l2_gas_amount: 1_000_000_000,
        max_calldata_length: 10,
        max_signature_length: 1,
        max_proof_size: 10,
        max_contract_bytecode_size: 100_000,
        max_contract_class_object_size: 100_000,
        min_sierra_version: *MIN_SIERRA_VERSION,
        max_sierra_version: *MAX_SIERRA_VERSION,
        allow_client_side_proving: true,
    });
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L148-151)
```rust
#[case::client_side_proving(
    DEFAULT_VALIDATOR_CONFIG_FOR_TESTING.clone(),
    RpcTransactionArgs { proof_facts: create_valid_proof_facts_for_testing(), proof: Proof::proof_for_testing(), ..Default::default()}
)]
```
