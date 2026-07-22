### Title
`estimate_fee`/`simulate_transactions` with `Tag::Pending` always uses `StarknetVersion::LATEST` VersionedConstants instead of the pending block's actual version — (`crates/apollo_rpc_execution/src/lib.rs`)

---

### Summary

When an unprivileged RPC client calls `estimate_fee` or `simulate_transactions` with `BlockId::Tag(Tag::Pending)`, the `VersionedConstants` selected for execution is always `StarknetVersion::LATEST` (the hardcoded latest in the binary), not the pending block's actual `starknet_version`. This is a concrete, reachable divergence that causes fee estimation to use the wrong step limits and gas cost tables.

---

### Finding Description

The call path is:

**Step 1 — `estimate_fee` in `api_impl.rs`:**

`maybe_pending_data` is populated from the pending block via `client_pending_data_to_execution_pending_data`. [1](#0-0) 

`block_number` is obtained via `get_accepted_block_number`, which for `Tag::Pending` returns the **latest accepted block number** (not the pending block number). [2](#0-1) 

**Step 2 — `client_pending_data_to_execution_pending_data` in `pending.rs`:**

The conversion copies gas prices, timestamp, sequencer, l1_da_mode, and state diff fields — but **does not copy `starknet_version`** from the pending block. [3](#0-2) 

**Step 3 — `ExecutionPendingData` struct in `objects.rs`:**

The struct has no `starknet_version` field at all. [4](#0-3) 

**Step 4 — `create_block_context` in `lib.rs`:**

When `maybe_pending_data` is `Some`, `block_number` is set to `block_context_number.unchecked_next()` (the pending block number, which is not yet in storage). [5](#0-4) 

Then `get_starknet_version(block_number)` is called on that pending block number. Because the pending block is not yet accepted, `get_starknet_version` returns `None` (the guard `block_number >= self.get_header_marker()` fires). [6](#0-5) 

The fallback is `StarknetVersion::LATEST`: [7](#0-6) 

`VersionedConstants::get(&starknet_version)?` then selects constants for `LATEST`, not for the pending block's actual version. [8](#0-7) 

`StarknetVersion::LATEST` is the last variant in the enum, currently `V0_14_4`. [9](#0-8) 

---

### Impact Explanation

If the network is running at version V0_13_3 (latest accepted block) and the pending block advertises V0_14_0, the binary's `StarknetVersion::LATEST` (V0_14_4) is used for `VersionedConstants` selection. The fee estimate returned to the client uses V0_14_4 step limits and gas cost tables instead of V0_13_3's or V0_14_0's. This is an authoritative-looking wrong value from a public RPC endpoint.

This matches the allowed impact: **High — RPC fee estimation returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

This condition is reachable whenever the binary's compiled-in `LATEST` version is ahead of the network's current version — a normal state during the window between a binary release and a network upgrade. No special privileges are required; any client calling `starknet_estimateFee` or `starknet_simulateTransactions` with `"block_id": "pending"` hits this path.

---

### Recommendation

Add a `starknet_version: Option<StarknetVersion>` field to `ExecutionPendingData`. Populate it in `client_pending_data_to_execution_pending_data` by parsing `client_pending_data.block.starknet_version()`. In `create_block_context`, when `maybe_pending_data` is `Some`, prefer `pending_data.starknet_version` over the storage lookup / `LATEST` fallback.

---

### Proof of Concept

Seed storage with a block at `StarknetVersion::V0_13_3`. Set `pending_data.block.starknet_version = "0.14.0"` (and `parent_block_hash` to match). Call `exec_estimate_fee` with `maybe_pending_data = Some(...)` and `block_number = N` (the accepted block). Inspect the `BlockContext` returned by `create_block_context`: `block_context.versioned_constants()` will correspond to `StarknetVersion::LATEST` (V0_14_4), not V0_14_0 or V0_13_3. The divergence is concrete and observable without any operator privileges.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1009-1016)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/block.rs (L129-131)
```rust
        BlockId::Tag(Tag::Latest | Tag::Pending) => {
            get_latest_block_number(txn)?.ok_or_else(|| ErrorObjectOwned::from(BLOCK_NOT_FOUND))?
        }
```

**File:** crates/apollo_rpc/src/pending.rs (L5-23)
```rust
pub(crate) fn client_pending_data_to_execution_pending_data(
    client_pending_data: ClientPendingData,
    pending_classes: PendingClasses,
) -> ExecutionPendingData {
    ExecutionPendingData {
        storage_diffs: client_pending_data.state_update.state_diff.storage_diffs,
        deployed_contracts: client_pending_data.state_update.state_diff.deployed_contracts,
        declared_classes: client_pending_data.state_update.state_diff.declared_classes,
        old_declared_contracts: client_pending_data.state_update.state_diff.old_declared_contracts,
        nonces: client_pending_data.state_update.state_diff.nonces,
        replaced_classes: client_pending_data.state_update.state_diff.replaced_classes,
        classes: pending_classes,
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
        sequencer: client_pending_data.block.sequencer_address(),
    }
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L487-510)
```rust
#[derive(Debug, Default, Clone, Eq, PartialEq)]
pub struct PendingData {
    // TODO(shahak): Consider indexing by address and key.
    /// All the contract storages that were changed in the pending block.
    pub storage_diffs: IndexMap<ContractAddress, Vec<StorageEntry>>,
    /// All the contracts that were deployed in the pending block.
    pub deployed_contracts: Vec<DeployedContract>,
    /// All the classes that were declared in the pending block.
    pub declared_classes: Vec<DeclaredClassHashEntry>,
    /// All the deprecated classes that were declared in the pending block.
    pub old_declared_contracts: Vec<ClassHash>,
    /// All the nonces that were changed in the pending block.
    pub nonces: IndexMap<ContractAddress, Nonce>,
    /// All the classes that were declared in the pending block.
    pub replaced_classes: Vec<ReplacedClass>,
    /// The timestamp of the pending block.
    pub timestamp: BlockTimestamp,
    /// The gas price of the pending block.
    pub l1_gas_price: GasPricePerToken,
    /// The data price of the pending block.
    pub l1_data_gas_price: GasPricePerToken,
    /// The L2 gas price of the pending block.
    pub l2_gas_price: GasPricePerToken,
    /// The data availability mode of the pending block.
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L341-342)
```rust
        Some(pending_data) => (
            block_context_number.unchecked_next(),
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L370-373)
```rust
    let starknet_version = storage_reader
        .begin_ro_txn()?
        .get_starknet_version(block_number)?
        .unwrap_or(StarknetVersion::LATEST);
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L408-408)
```rust
    let versioned_constants = VersionedConstants::get(&starknet_version)?;
```

**File:** crates/apollo_storage/src/header.rs (L256-258)
```rust
        if block_number >= self.get_header_marker()? {
            return Ok(None);
        }
```

**File:** crates/starknet_api/src/block.rs (L122-127)
```rust
    (V0_14_0, 0, 14, 0),
    (V0_14_1, 0, 14, 1),
    (V0_14_2, 0, 14, 2),
    (V0_14_3, 0, 14, 3),
    (V0_14_4, 0, 14, 4),
    V0_14_4
```
