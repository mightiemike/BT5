### Title
Hard-Coded `ChainId::Mainnet` Default in `ProverConfig` Silently Binds Wrong Chain ID for Transaction Hash Calculation on Non-Mainnet Deployments - (File: crates/starknet_transaction_prover/src/config.rs)

### Summary
`ProverConfig` in the `starknet_transaction_prover` production service declares `chain_id: ChainId::Mainnet` as its compile-time default. Because the struct carries `#[serde(default)]`, any deployment whose config file omits the `chain_id` key silently inherits `SN_MAIN`. The prover then passes this wrong chain ID into transaction hash calculation, producing hashes that are valid only on Ethereum mainnet and are therefore wrong for every other Starknet network (Sepolia, integration, custom L3s).

### Finding Description

`ProverConfig::default()` hard-codes the chain identifier:

```rust
// crates/starknet_transaction_prover/src/config.rs  lines 37-49
impl Default for ProverConfig {
    fn default() -> Self {
        Self {
            ...
            chain_id: ChainId::Mainnet,   // ← compile-time constant
            ...
        }
    }
}
``` [1](#0-0) 

The struct-level `#[serde(default)]` attribute means that if `chain_id` is absent from the operator's JSON/YAML config, Serde silently fills it with `ChainId::Mainnet` rather than returning a deserialization error. [2](#0-1) 

The `chain_id` stored in `ProverConfig` is the sole source of truth for the chain domain separator used when the prover converts an incoming `RpcTransaction` to an `InternalRpcTransaction`. The conversion path calls:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs  line 391
let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
``` [3](#0-2) 

`TransactionConverter` is constructed with the `chain_id` taken from `ProverConfig`. On a Sepolia or custom-network deployment where the operator forgets to set `chain_id`, the prover computes every transaction hash as if it were on `SN_MAIN`. The resulting `InternalRpcTransaction.tx_hash` is therefore the mainnet hash, not the network-correct hash.

The same `ChainId::Mainnet` default pattern appears in `DbConfig::default()`:

```rust
// crates/apollo_storage/src/db/mod.rs  lines 81-93
impl Default for DbConfig {
    fn default() -> Self {
        DbConfig {
            // TODO(guyn): should we remove the default for chain_id?
            chain_id: ChainId::Mainnet,
            ...
        }
    }
}
``` [4](#0-3) 

The storage path is `<path_prefix>/<chain_id>`, so a component that inherits `DbConfig::default()` without overriding `chain_id` opens the mainnet DB directory on a non-mainnet node. [5](#0-4) 

The benchmark binary `starknet_committer_cli` also hard-codes both storage configs as non-overridable `static LazyLock` values:

```rust
// crates/starknet_committer_cli/src/commands.rs  lines 53-91
static BATCHER_STORAGE_CONFIG: LazyLock<StorageConfig> = LazyLock::new(|| StorageConfig {
    db_config: DbConfig {
        path_prefix: PathBuf::from_str("/core-data/batcher").unwrap(),
        chain_id: ChainId::Mainnet,   // cannot be overridden
        ...
    },
    ...
});
``` [6](#0-5) 

### Impact Explanation

When the `starknet_transaction_prover` service runs on a non-mainnet network with a config that omits `chain_id`, every `RpcTransaction → InternalRpcTransaction` conversion computes the transaction hash using `SN_MAIN` as the domain separator instead of the actual network's chain ID. The stored `tx_hash` field inside `InternalRpcTransaction` is therefore the wrong value. Any downstream component that trusts this hash — proof generation, mempool deduplication, RPC responses — operates on an incorrect payload. This matches the **High** impact: *Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.*

### Likelihood Explanation

The `ProverConfig` struct carries `#[serde(default)]`, making the omission of `chain_id` a silent no-op rather than a config-load error. Operators deploying the prover on Sepolia or a custom Starknet network who do not explicitly add `chain_id` to their config file will silently receive mainnet hashes. The architecture explicitly targets future non-mainnet deployments, making this a realistic operational scenario.

### Recommendation

1. **Remove the `ChainId::Mainnet` default from `ProverConfig`**: Either delete the `Default` impl entirely or replace the `chain_id` field with `Option<ChainId>` and fail fast at startup if it is `None`.
2. **Add a startup assertion**: `assert!(address(chain_id).code.length > 0)` equivalent — validate that the configured chain ID resolves to a known network before accepting any transactions.
3. **Apply the same fix to `DbConfig::default()`**: The `TODO(guyn)` comment already flags this; remove `chain_id: ChainId::Mainnet` from the default so that any component that forgets to set it fails loudly rather than silently opening the mainnet DB path.

### Proof of Concept

1. Deploy `starknet_transaction_prover` on Starknet Sepolia (`SN_SEPOLIA`) with a config file that does **not** include a `chain_id` key.
2. Submit an `RpcInvokeTransactionV3` whose correct Sepolia hash is `H_sepolia`.
3. Observe that `ProverConfig::default()` fills `chain_id = ChainId::Mainnet` via `#[serde(default)]`.
4. `convert_rpc_tx_to_internal` calls `calculate_transaction_hash(&ChainId::Mainnet)`, producing `H_mainnet ≠ H_sepolia`.
5. The `InternalRpcTransaction` stored and forwarded to the proof pipeline carries `tx_hash = H_mainnet` — the wrong hash for the actual network — silently corrupting every downstream hash-dependent operation. [7](#0-6) [3](#0-2)

### Citations

**File:** crates/starknet_transaction_prover/src/config.rs (L11-17)
```rust
#[serde(default)]
pub struct ProverConfig {
    /// Configuration for the contract class manager.
    pub contract_class_manager_config: ContractClassManagerConfig,
    /// Chain ID of the network.
    pub chain_id: ChainId,
    /// RPC node URL for fetching state.
```

**File:** crates/starknet_transaction_prover/src/config.rs (L37-49)
```rust
impl Default for ProverConfig {
    fn default() -> Self {
        Self {
            contract_class_manager_config: ContractClassManagerConfig::default(),
            chain_id: ChainId::Mainnet,
            rpc_node_url: String::new(),
            runner_config: RunnerConfig::default(),
            strk_fee_token_address: None,
            validate_zero_fee_fields: true,
            blocking_check_url: None,
            blocking_check_timeout_millis: 2000,
            blocking_check_fail_open: true,
        }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/apollo_storage/src/db/mod.rs (L81-93)
```rust
impl Default for DbConfig {
    fn default() -> Self {
        DbConfig {
            path_prefix: PathBuf::from("./data"),
            // TODO(guyn): should we remove the default for chain_id?
            chain_id: ChainId::Mainnet,
            enforce_file_exists: false,
            min_size: 1 << 20,    // 1MB
            max_size: 1 << 40,    // 1TB
            growth_step: 1 << 32, // 4GB
            max_readers: 1 << 13, // 8K readers
        }
    }
```

**File:** crates/apollo_storage/src/db/mod.rs (L148-153)
```rust
impl DbConfig {
    /// Returns the path of the database (path prefix, followed by the chain id).
    pub fn path(&self) -> PathBuf {
        self.path_prefix.join(self.chain_id.to_string().as_str())
    }
}
```

**File:** crates/starknet_committer_cli/src/commands.rs (L53-91)
```rust
// This is based on the batcher's storage configuration on mainnet.
static BATCHER_STORAGE_CONFIG: LazyLock<StorageConfig> = LazyLock::new(|| StorageConfig {
    db_config: DbConfig {
        path_prefix: PathBuf::from_str("/core-data/batcher").unwrap(),
        chain_id: ChainId::Mainnet,
        enforce_file_exists: true,
        min_size: 1048576,
        max_size: 1099511627776,
        growth_step: 67108864,
        max_readers: 8192,
    },
    mmap_file_config: MmapFileConfig {
        max_size: 1099511627776,
        growth_step: 2147483648,
        max_object_size: 1073741824,
    },
    scope: StorageScope::StateOnly,
    batch_config: Default::default(),
});

// This is based on the state sync's storage configuration on mainnet.
static STATE_SYNC_STORAGE_CONFIG: LazyLock<StorageConfig> = LazyLock::new(|| StorageConfig {
    db_config: DbConfig {
        path_prefix: PathBuf::from_str("/core-data/state_sync").unwrap(),
        chain_id: ChainId::Mainnet,
        enforce_file_exists: true,
        min_size: 1048576,
        max_size: 1099511627776,
        growth_step: 67108864,
        max_readers: 8192,
    },
    mmap_file_config: MmapFileConfig {
        max_size: 1099511627776,
        growth_step: 2147483648,
        max_object_size: 1073741824,
    },
    scope: StorageScope::FullArchive,
    batch_config: Default::default(),
});
```
