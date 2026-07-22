### Title
`ExecutionConfig::default()` hardcodes mainnet fee-token addresses, causing wrong fee estimation and simulation on non-mainnet RPC deployments — (`crates/apollo_rpc_execution/src/lib.rs`)

### Summary

`ExecutionConfig::default()` bakes in the Starknet **mainnet** STRK and ETH fee-token contract addresses. The state-sync deployment config (`state_sync_config.json`) omits these fields entirely, so any standalone state-sync / RPC node that does not receive the full-node pointer-target override silently inherits the mainnet addresses. On Sepolia or any custom L3, those addresses hold no contract; every call to `estimate_fee`, `simulate_transactions`, or `starknet_call` that charges or inspects fees builds a `BlockContext` with the wrong `FeeTokenAddresses`, returning authoritative-looking but incorrect values.

### Finding Description

**Root cause — hardcoded mainnet defaults** [1](#0-0) 

```rust
const STRK_FEE_CONTRACT_ADDRESS_STR: &str =
    "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d";   // mainnet only
const ETH_FEE_CONTRACT_ADDRESS_STR: &str =
    "0x49d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7";    // mainnet only
```

These constants seed the `Default` implementation: [2](#0-1) 

**Propagation into every RPC execution call**

`create_block_context()` copies the addresses from `ExecutionConfig` directly into the `ChainInfo` that is handed to the blockifier: [3](#0-2) 

This `BlockContext` is used by every public RPC execution path: [4](#0-3) [5](#0-4) 

**Missing fields in the deployment config**

The standalone state-sync config file contains `default_initial_gas_cost` but **no** `strk_fee_contract_address` or `eth_fee_contract_address`: [6](#0-5) 

The replacer template is identical in this respect: [7](#0-6) 

The full-node `config_schema.json` wires these fields to pointer targets (`strk_fee_token_address`, `eth_fee_token_address`), but only when the **full** node config is assembled: [8](#0-7) 

In a distributed deployment where the state-sync component is started with only `state_sync_config.json`, the pointer-target resolution never runs, and `ExecutionConfig` falls back to its `Default` — the mainnet addresses.

**Analog to H-10**: just as the Canto Comptroller's `getWETHAddress()` returned a hardcoded Ethereum mainnet address that was meaningless on Canto, `ExecutionConfig::default()` returns hardcoded Starknet mainnet fee-token addresses that are meaningless on Sepolia or any L3.

### Impact Explanation

On a non-mainnet deployment using the standalone state-sync config:

- `estimate_fee` builds a `BlockContext` whose `fee_token_addresses` point to non-existent contracts. The blockifier's fee-charging logic calls into those addresses; the call either silently returns zero (no contract) or reverts. The RPC response is a wrong fee value presented as authoritative.
- `simulate_transactions` and `starknet_call` suffer the same wrong `ChainInfo`, producing incorrect execution traces and return values for any fee-token interaction.
- The `virtual_os_config_hash` cached inside `BlockContext` is computed from `strk_fee_token_address`; a wrong address produces a wrong hash that diverges from the on-chain OS config hash, breaking proof verification for client-side proving flows.

This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

The distributed deployment topology is explicitly supported and documented. The state-sync component's own config file is the natural starting point for operators running it standalone. Because neither `state_sync_config.json` nor `replacer_state_sync_config.json` includes the fee-token address fields, any non-mainnet operator who follows the provided templates will silently inherit the mainnet defaults. No privileged access or malicious input is required; a normal `estimate_fee` RPC call is sufficient to trigger the wrong path.

### Recommendation

1. Remove the hardcoded mainnet constants from `ExecutionConfig::default()`. Replace them with `ContractAddress::default()` (zero) and add a startup validator that rejects zero addresses, forcing explicit configuration.
2. Add `strk_fee_contract_address` and `eth_fee_contract_address` (or their replacer placeholders) to `state_sync_config.json` and `replacer_state_sync_config.json` so standalone deployments are always explicitly configured.
3. Alternatively, derive the fee-token addresses from `chain_id` inside `create_block_context()` (as `blockifier_reexecution` already does in `get_fee_token_addresses`) and remove the fields from `ExecutionConfig` entirely.

### Proof of Concept

1. Deploy the state-sync component on Sepolia using only `state_sync_config.json` (no full-node config, no pointer-target resolution).
2. `ExecutionConfig` loads with `strk_fee_contract_address = 0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d` (mainnet STRK — does not exist on Sepolia).
3. Issue `starknet_estimateFee` for any V3 transaction.
4. `create_block_context()` constructs `ChainInfo { fee_token_addresses: { strk: <mainnet addr>, eth: <mainnet addr> } }`.
5. The blockifier attempts to read the fee-token balance at the mainnet address; the contract does not exist on Sepolia, so the storage read returns zero.
6. The returned fee estimate is `0` (or an unexpected revert), diverging from the correct Sepolia fee — an authoritative-looking wrong value delivered to the caller.

### Citations

**File:** crates/apollo_rpc_execution/src/lib.rs (L94-121)
```rust
/// The address of the STRK fee contract on Starknet.
const STRK_FEE_CONTRACT_ADDRESS_STR: &str =
    "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d";
/// The address of the ETH fee contract on Starknet.
const ETH_FEE_CONTRACT_ADDRESS_STR: &str =
    "0x49d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7";
const DEFAULT_INITIAL_GAS_COST: u64 = 10000000000;

/// Result type for execution functions.
pub type ExecutionResult<T> = Result<T, ExecutionError>;

/// The address of the STRK fee contract on Starknet.
pub static STRK_FEE_CONTRACT_ADDRESS: LazyLock<ContractAddress> = LazyLock::new(|| {
    ContractAddress::try_from(
        Felt::from_hex(STRK_FEE_CONTRACT_ADDRESS_STR)
            .expect("Error converting strk fee contract address from hex"),
    )
    .expect("Error converting strk fee contract address from felt")
});

/// The address of the ETH fee contract on Starknet.
pub static ETH_FEE_CONTRACT_ADDRESS: LazyLock<ContractAddress> = LazyLock::new(|| {
    ContractAddress::try_from(
        Felt::from_hex(ETH_FEE_CONTRACT_ADDRESS_STR)
            .expect("Error converting eth fee contract address from hex"),
    )
    .expect("Error converting eth fee contract address from felt")
});
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L134-142)
```rust
impl Default for ExecutionConfig {
    fn default() -> Self {
        ExecutionConfig {
            strk_fee_contract_address: *STRK_FEE_CONTRACT_ADDRESS,
            eth_fee_contract_address: *ETH_FEE_CONTRACT_ADDRESS,
            default_initial_gas_cost: DEFAULT_INITIAL_GAS_COST,
        }
    }
}
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L268-276)
```rust
    let block_context = create_block_context(
        &mut cached_state,
        block_context_number,
        chain_id.clone(),
        &storage_reader,
        maybe_pending_data.as_ref(),
        execution_config,
        override_kzg_da_to_false,
    )?;
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L400-407)
```rust
    let chain_info = ChainInfo {
        chain_id,
        fee_token_addresses: FeeTokenAddresses {
            strk_fee_token_address: execution_config.strk_fee_contract_address,
            eth_fee_token_address: execution_config.eth_fee_contract_address,
        },
        is_l3: false,
    };
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L701-709)
```rust
    let block_context = create_block_context(
        &mut cached_state,
        block_context_block_number,
        chain_id.clone(),
        &storage_reader,
        maybe_pending_data.as_ref(),
        execution_config,
        override_kzg_da_to_false,
    )?;
```

**File:** crates/apollo_deployments/resources/app_configs/state_sync_config.json (L50-54)
```json
  "state_sync_config.static_config.rpc_config.execution_config.default_initial_gas_cost": 10000000000,
  "state_sync_config.static_config.rpc_config.ip": "0.0.0.0",
  "state_sync_config.static_config.rpc_config.max_events_chunk_size": 1000,
  "state_sync_config.static_config.rpc_config.max_events_keys": 100,
  "state_sync_config.static_config.rpc_config.port": "",
```

**File:** crates/apollo_deployments/resources/app_configs/replacer_state_sync_config.json (L51-51)
```json
  "state_sync_config.static_config.rpc_config.execution_config.default_initial_gas_cost": 10000000000,
```

**File:** crates/apollo_node/resources/config_schema.json (L4012-4021)
```json
  "state_sync_config.static_config.rpc_config.execution_config.eth_fee_contract_address": {
    "description": "The eth fee token address to receive fees",
    "pointer_target": "eth_fee_token_address",
    "privacy": "Public"
  },
  "state_sync_config.static_config.rpc_config.execution_config.strk_fee_contract_address": {
    "description": "The strk fee token address to receive fees",
    "pointer_target": "strk_fee_token_address",
    "privacy": "Public"
  },
```
