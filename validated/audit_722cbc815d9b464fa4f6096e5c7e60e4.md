### Title
Gateway `validate_resource_bounds` Rejects Valid Client-Side Proving Transactions with Zero-Price Resource Bounds — (File: `crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

`StatelessTransactionValidator::validate_resource_bounds` checks `max_possible_fee(Tip::ZERO) == Fee(0)` and rejects the transaction with `ZeroResourceBounds`. Client-side proving transactions are required by the prover to have all `max_price_per_unit` fields set to zero. This creates a direct incompatibility: the prover mandates zero prices, but the gateway rejects zero prices, causing every valid client-side proving transaction to be rejected at the gateway admission stage when `validate_resource_bounds: true`.

### Finding Description

In `crates/apollo_gateway/src/stateless_transaction_validator.rs` lines 64–68:

```rust
let resource_bounds = *tx.resource_bounds();
// The resource bounds should be positive even without the tip.
if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
    return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
}
``` [1](#0-0) 

For a client-side proving transaction the prover's `validate_zero_fee_resource_bounds` (lines 401–445 of `virtual_snos_prover.rs`) explicitly mandates:

- All `max_price_per_unit` fields **must be zero** ("Proving is client-side — no fees are charged").
- `tip` **must be zero**.
- `l2_gas.max_amount` **must be non-zero** (it is the OS gas limit, e.g. `0x5f5e100`). [2](#0-1) 

With those values, `max_possible_fee(Tip::ZERO)` = `l1_gas.max_amount × 0 + l2_gas.max_amount × 0 + l1_data_gas.max_amount × 0` = `0`, so the condition on line 66 evaluates to `true` and the transaction is rejected with `ZeroResourceBounds` before it ever reaches the `validate_client_side_proving_allowed` check.

The real-world block data in `block_post_0_14_3.json` confirms that client-side proving transactions carry exactly this shape (all prices zero, `l2_gas.max_amount = 0x5f5e100`, non-empty `proof_facts`): [3](#0-2) 

The test suite itself reveals the incompatibility: the `client_side_proving` positive-flow test case is forced to use `DEFAULT_VALIDATOR_CONFIG_FOR_TESTING` with `validate_resource_bounds: false` — the only way to make it pass — while the production-default `DEFAULT_VALIDATOR_CONFIG` (used in `test_invalid_resource_bounds`) has `validate_resource_bounds: true`: [4](#0-3) [5](#0-4) 

The `validate_resource_bounds` call is unconditional for all transaction types and runs before any client-side-proving-specific logic: [6](#0-5) 

The `StatelessTransactionValidatorConfig` field comment ("If true, ensures that at least one resource bound (L1, L2, or L1 data) is greater than zero") is also misleading: the actual check tests whether the *fee* is non-zero, not whether any *amount* is non-zero. A transaction with `l2_gas.max_amount = 100_000_000` and `l2_gas.max_price_per_unit = 0` has a non-zero amount but a zero fee, and is incorrectly rejected. [7](#0-6) 

### Impact Explanation

Every client-side proving transaction submitted to a gateway node with `validate_resource_bounds: true` (the production default) is rejected at stateless validation before it reaches the mempool. The feature is silently broken for any deployment that enables both `validate_resource_bounds` and `allow_client_side_proving`. This matches: **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

### Likelihood Explanation

High. The production-default `StatelessTransactionValidatorConfig` has `validate_resource_bounds: true` (demonstrated by `DEFAULT_VALIDATOR_CONFIG` in the test suite). Client-side proving is a live production feature (real transactions with `proof_facts` exist in committed blocks). Any operator running the default configuration with client-side proving enabled will silently drop every such transaction.

### Recommendation

In `validate_resource_bounds`, add a special-case bypass for client-side proving transactions: if the transaction is an `RpcInvokeTransaction::V3` with non-empty `proof_facts`, skip the `max_possible_fee == 0` check (and the `min_gas_price` check on `l2_gas.max_price_per_unit`) and instead verify only that `l2_gas.max_amount > 0`. Alternatively, restructure the validation order so that `validate_client_side_proving_allowed` runs first and the resource-bounds check is skipped for proven transactions.

### Proof of Concept

1. Construct an `RpcInvokeTransactionV3` with `l2_gas.max_amount = 100_000_000`, all `max_price_per_unit = 0`, `tip = 0`, and a non-empty `proof_facts` vector (exactly the format the prover produces and `block_post_0_14_3.json` confirms).
2. Submit to a gateway configured with `validate_resource_bounds: true` and `allow_client_side_proving: true`.
3. `validate_resource_bounds` computes `max_possible_fee(Tip::ZERO) = 0`, enters the branch at line 67, and returns `Err(ZeroResourceBounds)`.
4. The transaction is rejected before reaching `validate_client_side_proving_allowed` or the mempool, despite being a fully valid client-side proving transaction.

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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L64-69)
```rust
        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }
```

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L401-443)
```rust
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
```

**File:** crates/apollo_starknet_client/resources/reader/block_post_0_14_3.json (L134-170)
```json
            "resource_bounds": {
                "L1_GAS": {
                    "max_amount": "0x0",
                    "max_price_per_unit": "0x0"
                },
                "L2_GAS": {
                    "max_amount": "0x5f5e100",
                    "max_price_per_unit": "0x0"
                },
                "L1_DATA_GAS": {
                    "max_amount": "0x0",
                    "max_price_per_unit": "0x0"
                }
            },
            "tip": "0x0",
            "paymaster_data": [],
            "sender_address": "0x5ff40f171ca21540e4840c4210c24db9419e4466eec475aa3133d0a6f977c45",
            "calldata": [
                "0x1",
                "0x70a5da4f557b77a9c54546e4bcc900806e28793d8e3eaaa207428d2387249b7",
                "0x83afd3f4caedc6eebf44246fe54e38c95e3179a5ec9ea81740eca5b482d12e",
                "0x3",
                "0x2653cf3f8f8af76f0f8fe17fc095e2e8fd6b1fddf8931e198be7ff033c0ec2e",
                "0x0",
                "0x0"
            ],
            "account_deployment_data": [],
            "proof_facts": [
                "0x50524f4f4630",
                "0x5649525455414c5f534e4f53",
                "0x9743416d2d92b680d47338cb89f3def2e77ba772bbc2e568aeb48425e6c450",
                "0x5649525455414c5f534e4f5330",
                "0xf361e",
                "0x59fd5060b341eef5d3225eb5c9c7c7cbb468ac509317e116505bdbe7edd08e",
                "0x6989a681c469d769f3a706c56550a63741a4b2d32bef4b1209a26daad1dbb6",
                "0x0"
            ],
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L148-158)
```rust
#[case::client_side_proving(
    DEFAULT_VALIDATOR_CONFIG_FOR_TESTING.clone(),
    RpcTransactionArgs { proof_facts: create_valid_proof_facts_for_testing(), proof: Proof::proof_for_testing(), ..Default::default()}
)]
#[case::client_side_proving_disabled(
    StatelessTransactionValidatorConfig {
        allow_client_side_proving: false,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs::default()
)]
```

**File:** crates/apollo_gateway_config/src/config.rs (L166-186)
```rust
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, Validate)]
pub struct StatelessTransactionValidatorConfig {
    // If true, ensures that at least one resource bound (L1, L2, or L1 data) is greater than zero.
    pub validate_resource_bounds: bool,
    // TODO(AlonH): Remove the `min_gas_price` field from this struct and use the one from the
    // versioned constants.
    pub min_gas_price: u128,
    pub max_l2_gas_amount: u64,
    pub max_calldata_length: usize,
    pub max_signature_length: usize,
    pub max_proof_size: usize,

    // Declare txs specific config.
    pub max_contract_bytecode_size: usize,
    pub max_contract_class_object_size: usize,
    pub min_sierra_version: VersionId,
    pub max_sierra_version: VersionId,

    // If true, allows transactions with non-empty proof_facts or proof fields.
    pub allow_client_side_proving: bool,
}
```
