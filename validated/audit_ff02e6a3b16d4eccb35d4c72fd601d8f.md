### Title
`StatelessTransactionValidatorConfig` Lacks Cross-Field Validation Ensuring `min_sierra_version ≤ max_sierra_version`, Causing All Declare Transactions to Be Permanently Rejected at Gateway Admission - (`crates/apollo_gateway_config/src/config.rs`)

### Summary
`StatelessTransactionValidatorConfig` derives `Validate` but contains no cross-field constraint enforcing `min_sierra_version ≤ max_sierra_version`. If an operator sets `min_sierra_version > max_sierra_version` (e.g., via a deployment config file or environment override), the `validate_sierra_version` check inside `StatelessTransactionValidator` will reject every declare transaction regardless of its actual Sierra version, because no version can simultaneously satisfy `min ≤ version ≤ max` when `min > max`. The gateway's config validation chain (`GatewayStaticConfig → StatelessTransactionValidatorConfig`) does not catch this inversion at startup, so the node silently enters a state where all declare transactions are permanently rejected.

### Finding Description
`StatelessTransactionValidatorConfig` at `crates/apollo_gateway_config/src/config.rs:166–186` holds two independent fields:

```rust
pub min_sierra_version: VersionId,
pub max_sierra_version: VersionId,
```

The struct derives `Validate` but carries no `#[validate(custom = "...")]` or any other cross-field rule. `GatewayStaticConfig` propagates validation via `#[validate(nested)]` on `stateless_tx_validator_config`, but because `StatelessTransactionValidatorConfig` itself has no cross-field rule, the inversion `min > max` passes all validation checks silently.

At runtime, `validate_sierra_version` in `crates/apollo_gateway/src/stateless_transaction_validator.rs:293–313` evaluates:

```rust
let mut max_sierra_version = self.config.max_sierra_version;
max_sierra_version.0.patch = usize::MAX;          // patch is widened to MAX

let sierra_version = VersionId::from_sierra_program(sierra_program)?;
if self.config.min_sierra_version <= sierra_version && sierra_version <= max_sierra_version {
    return Ok(());
}
Err(StatelessTransactionValidatorError::UnsupportedSierraVersion { ... })
```

When `min_sierra_version > max_sierra_version` (e.g., `min = 1.9.0`, `max = 1.1.0`):

- For any `sierra_version ≥ 1.9.0`: the left predicate `min ≤ version` is true, but `version ≤ 1.1.MAX` is false.
- For any `sierra_version < 1.9.0`: the left predicate `min ≤ version` is false.

No Sierra version can satisfy both predicates simultaneously. Every declare transaction is rejected with `UnsupportedSierraVersion` at the stateless gateway admission layer, before any stateful check, signature verification, or sequencing occurs.

The default deployment configs (`crates/apollo_deployments/resources/app_configs/gateway_config.json`) set `min = 1.1.0` and `max = 1.9.0`, which is correct. However, the replacer template (`replacer_gateway_config.json`) substitutes `max_sierra_version.patch` from an environment variable (`$$$_..._$$$`), and neither the config loader nor the `Validate` derive will catch an inverted pair.

### Impact Explanation
**High. Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

All declare transactions — regardless of their actual Sierra version — are rejected at the stateless gateway admission layer. No new contract classes can be declared on the network. This is a complete denial-of-service of the declare functionality, triggered silently at node startup with no error logged at the config-validation stage.

### Likelihood Explanation
**Low-Medium.** The trigger requires an operator to deploy a misconfigured `min_sierra_version > max_sierra_version` pair. This can happen via:
- A deployment automation error substituting the wrong value for `max_sierra_version.minor` or `max_sierra_version.major` in the replacer template.
- A manual config override that inverts the bounds.
- A future config migration that changes one field without updating the other.

Because no validation gate catches the inversion at startup, the misconfiguration is not surfaced until declare transactions begin failing in production.

### Recommendation
Add a cross-field custom validator to `StatelessTransactionValidatorConfig` that asserts `min_sierra_version ≤ max_sierra_version` and returns a `ValidationError` if violated. This should be wired into the existing `Validate` derive so that `validate_node_config()` / `config_validate()` catches the inversion at node startup, before any transactions are processed.

```rust
fn validate_sierra_version_range(config: &StatelessTransactionValidatorConfig)
    -> Result<(), ValidationError>
{
    if config.min_sierra_version > config.max_sierra_version {
        return Err(ValidationError::new(
            "min_sierra_version must be <= max_sierra_version"
        ));
    }
    Ok(())
}
```

### Proof of Concept
Set the following in the gateway config:

```json
"gateway_config.static_config.stateless_tx_validator_config.min_sierra_version.major": 1,
"gateway_config.static_config.stateless_tx_validator_config.min_sierra_version.minor": 9,
"gateway_config.static_config.stateless_tx_validator_config.min_sierra_version.patch": 0,
"gateway_config.static_config.stateless_tx_validator_config.max_sierra_version.major": 1,
"gateway_config.static_config.stateless_tx_validator_config.max_sierra_version.minor": 1,
"gateway_config.static_config.stateless_tx_validator_config.max_sierra_version.patch": 0
```

Node starts without error. Submit any declare transaction with a valid Sierra program at version `1.7.0`. The gateway returns `UnsupportedSierraVersion { version: 1.7.0, min_version: 1.9.0, max_version: 1.1.0 }`. Repeat for every Sierra version in `[1.0.0, 2.0.0)` — all are rejected. The declare pathway is completely blocked.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_gateway_config/src/config.rs (L40-58)
```rust
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, Validate)]
pub struct GatewayStaticConfig {
    #[validate(nested)]
    pub stateless_tx_validator_config: StatelessTransactionValidatorConfig,
    #[validate(nested)]
    pub stateful_tx_validator_config: StatefulTransactionValidatorConfig,
    #[validate(nested)]
    pub contract_class_manager_config: ContractClassManagerConfig,
    pub chain_info: ChainInfo,
    pub block_declare: bool,
    #[serde(default, deserialize_with = "deserialize_comma_separated_str")]
    pub authorized_declarer_accounts: Option<Vec<ContractAddress>>,
    /// Maximum number of Sierra-to-CASM compilations (triggered by declare transactions) allowed
    /// to run concurrently. Declares that arrive while this limit is reached are rejected
    /// immediately rather than queued.
    #[validate(range(min = 1))]
    pub max_concurrent_declare_compilations: usize,
    pub proof_archive_writer_config: ProofArchiveWriterConfig,
}
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L293-313)
```rust
    fn validate_sierra_version(
        &self,
        sierra_program: &[Felt],
    ) -> StatelessTransactionValidatorResult<()> {
        // Any patch version is valid. (i.e. when check version for upper bound, we ignore the Z
        // part in a version X.Y.Z).
        let mut max_sierra_version = self.config.max_sierra_version;
        max_sierra_version.0.patch = usize::MAX;

        let sierra_version = VersionId::from_sierra_program(sierra_program)?;
        if self.config.min_sierra_version <= sierra_version && sierra_version <= max_sierra_version
        {
            return Ok(());
        }

        Err(StatelessTransactionValidatorError::UnsupportedSierraVersion {
            version: sierra_version,
            min_version: self.config.min_sierra_version,
            max_version: self.config.max_sierra_version,
        })
    }
```

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L27-34)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_sierra_version.major": 1,
  "gateway_config.static_config.stateless_tx_validator_config.max_sierra_version.minor": 9,
  "gateway_config.static_config.stateless_tx_validator_config.max_sierra_version.patch": 0,
  "gateway_config.static_config.stateless_tx_validator_config.max_signature_length": 4000,
  "gateway_config.static_config.stateless_tx_validator_config.min_gas_price": 8000000000,
  "gateway_config.static_config.stateless_tx_validator_config.min_sierra_version.major": 1,
  "gateway_config.static_config.stateless_tx_validator_config.min_sierra_version.minor": 1,
  "gateway_config.static_config.stateless_tx_validator_config.min_sierra_version.patch": 0
```
