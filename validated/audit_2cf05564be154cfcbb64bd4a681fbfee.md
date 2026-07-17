### Title
Orphaned `GlobalContractCode` Trie Entry After Account Deletion Enables Global-Contract Hijacking — (`File: core/store/src/utils/mod.rs`, `runtime/runtime/src/global_contracts.rs`)

---

### Summary

When an account deploys a global contract using `GlobalContractDeployMode::AccountId` and later deletes that account, the `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId(account_id) }` entry is never removed from the trie. `remove_account` only cleans up account-scoped keys (Account, ContractCode, AccessKey, ContractData). Because NEAR account IDs can be re-registered after deletion, any actor who re-registers the deleted account ID can deploy a new (malicious) global contract under the same identifier, silently redirecting execution for every account that holds `AccountContract::GlobalByAccount(deleted_account_id)` in its state.

---

### Finding Description

`GlobalContractDeployMode::AccountId` stores the contract under `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId(account_id) }` on every shard. The design intent is that the account owner can update the contract for all users. [1](#0-0) 

`action_delete_account` calls `remove_account`, which removes the account record, local contract code, access keys, and contract data — but **not** the global contract code or nonce entries: [2](#0-1) 

After deletion, `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId(account_id) }` and `TrieKey::GlobalContractNonce { identifier: GlobalContractCodeIdentifier::AccountId(account_id) }` remain permanently in the trie. [3](#0-2) 

When a new account re-registers the same account ID and calls `DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId`, `initiate_distribution` constructs the identifier from the new account's ID: [4](#0-3) 

`increment_nonce` reads the orphaned nonce from the trie and increments it, producing a fresh nonce that passes the freshness check: [5](#0-4) 

`apply_distribution_current_shard` then overwrites the trie entry with the attacker's code: [6](#0-5) 

At execution time, `RuntimeContractIdentifier::resolve` looks up `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId(account_id) }` to obtain the code hash for any account holding `AccountContract::GlobalByAccount(account_id)`: [7](#0-6) 

After the attacker's deployment, this lookup returns the attacker's code hash, and the attacker's WASM runs in the context of every victim account.

---

### Impact Explanation

Every account that executed `UseGlobalContractAction` with `GlobalContractIdentifier::AccountId(deleted_account_id)` stores `AccountContract::GlobalByAccount(deleted_account_id)` in its on-trie state: [8](#0-7) 

After the attacker re-registers the account and deploys malicious code, any function call on a victim account executes the attacker's WASM with full access to the victim's storage and balance. This is a supply-chain attack: one re-registration silently compromises an unbounded number of accounts. Additionally, the orphaned `GlobalContractCode` and `GlobalContractNonce` trie entries (up to 4 MiB of WASM per shard) constitute a permanent storage leak with no recovery path.

---

### Likelihood Explanation

The preconditions are:
1. The original deployer deletes their account (possible whenever locked balance is zero — no staking).
2. An attacker re-registers the same account ID before anyone else.

Account deletion is a normal, unprivileged protocol operation. Re-registration costs ~0.007 NEAR (post `AccountCostIncrease`, protocol version 85). The attacker does not need any special role. The window between deletion and re-registration is bounded only by block time, making front-running feasible on a monitored chain. No existing gate in `action_delete_account` checks for a deployed `GlobalContractDeployMode::AccountId` contract: [9](#0-8) 

---

### Recommendation

`remove_account` should also remove `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId(account_id) }` and `TrieKey::GlobalContractNonce { identifier: GlobalContractCodeIdentifier::AccountId(account_id) }` when deleting an account. Alternatively, `action_delete_account` should reject deletion if the account has a live `GlobalContractDeployMode::AccountId` entry in the trie, analogous to the existing rejection when locked balance is non-zero.

---

### Proof of Concept

```
1. alice.near calls DeployGlobalContractAction {
       code: <legitimate_wasm>,
       deploy_mode: GlobalContractDeployMode::AccountId
   }
   → TrieKey::GlobalContractCode { AccountId("alice.near") } = <legitimate_wasm>
   → TrieKey::GlobalContractNonce { AccountId("alice.near") } = 1

2. bob.near calls UseGlobalContractAction {
       contract_identifier: GlobalContractIdentifier::AccountId("alice.near")
   }
   → bob.near account state: AccountContract::GlobalByAccount("alice.near")

3. alice.near calls DeleteAccountAction { beneficiary_id: "charlie.near" }
   → remove_account("alice.near") removes Account, ContractCode, AccessKey, ContractData
   → TrieKey::GlobalContractCode { AccountId("alice.near") } REMAINS in trie
   → TrieKey::GlobalContractNonce { AccountId("alice.near") } REMAINS in trie (= 1)

4. attacker registers alice.near (costs ~0.007 NEAR)

5. attacker calls DeployGlobalContractAction {
       code: <malicious_wasm>,
       deploy_mode: GlobalContractDeployMode::AccountId
   }
   → increment_nonce reads orphaned nonce 1, writes 2
   → distribution receipt carries nonce 2 (fresh)
   → TrieKey::GlobalContractCode { AccountId("alice.near") } = <malicious_wasm>

6. Any FunctionCall on bob.near:
   → RuntimeContractIdentifier::resolve reads GlobalContractCode { AccountId("alice.near") }
   → returns hash of <malicious_wasm>
   → attacker's WASM executes in bob.near's context
```

### Citations

**File:** core/primitives/src/action/mod.rs (L133-141)
```rust
pub enum GlobalContractDeployMode {
    /// Contract is deployed under its code hash.
    /// Users will be able reference it by that hash.
    /// This effectively makes the contract immutable.
    CodeHash,
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
```

**File:** core/store/src/utils/mod.rs (L487-556)
```rust
pub fn remove_account(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
) -> Result<RemoveAccountResult, StorageError> {
    state_update.remove(TrieKey::Account { account_id: account_id.clone() });
    state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });

    let mut gas_key_nonce_count: usize = 0;
    let mut gas_key_nonce_total_key_bytes: usize = 0;

    // Removing access keys and gas key nonces
    let lock = state_update.trie().lock_for_iter();
    let mut keys_to_remove: Vec<TrieKey> = Vec::new();
    for raw_key in state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_access_keys(account_id), &lock)?
    {
        let raw_key = raw_key?;
        let key_handle = trie_key_parsers::parse_key_handle_from_access_key_key(
            &raw_key, account_id,
        )
        .map_err(|_e| {
            StorageError::StorageInconsistentState(
                "Can't parse key handle from raw key for AccessKey".to_string(),
            )
        })?;
        let nonce_index =
            trie_key_parsers::parse_nonce_index_from_gas_key_key(&raw_key, account_id, &key_handle)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse nonce index from raw key for AccessKey".to_string(),
                    )
                })?;
        if let Some(index) = nonce_index {
            gas_key_nonce_count += 1;
            gas_key_nonce_total_key_bytes += raw_key.len();
            keys_to_remove.push(TrieKey::gas_key_nonce(
                account_id.clone(),
                key_handle.clone(),
                index,
            ));
        } else {
            keys_to_remove.push(TrieKey::access_key(account_id.clone(), key_handle.clone()));
        }
    }
    drop(lock);

    for trie_key in keys_to_remove {
        state_update.remove(trie_key);
    }

    // Removing contract data
    let lock = state_update.trie().lock_for_iter();
    let data_keys = state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_contract_data(account_id, &[]), &lock)?
        .map(|raw_key| {
            trie_key_parsers::parse_data_key_from_contract_data_key(&raw_key?, account_id)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse data key from raw key for ContractData".to_string(),
                    )
                })
                .map(Vec::from)
        })
        .collect::<Result<Vec<_>, _>>()?;
    drop(lock);

    for key in data_keys {
        state_update.remove(TrieKey::ContractData { account_id: account_id.clone(), key });
    }
    Ok(RemoveAccountResult { gas_key_nonce_count, gas_key_nonce_total_key_bytes })
```

**File:** core/primitives/src/trie_key.rs (L104-127)
```rust
    pub const ALL_COLUMNS_WITH_NAMES: [(u8, &'static str); 22] = [
        (ACCOUNT, "Account"),
        (CONTRACT_CODE, "ContractCode"),
        (ACCESS_KEY, "AccessKey"),
        (RECEIVED_DATA, "ReceivedData"),
        (POSTPONED_RECEIPT_ID, "PostponedReceiptId"),
        (PENDING_DATA_COUNT, "PendingDataCount"),
        (POSTPONED_RECEIPT, "PostponedReceipt"),
        (DELAYED_RECEIPT_OR_INDICES, "DelayedReceiptOrIndices"),
        (CONTRACT_DATA, "ContractData"),
        (PROMISE_YIELD_INDICES, "PromiseYieldIndices"),
        (PROMISE_YIELD_TIMEOUT, "PromiseYieldTimeout"),
        (PROMISE_YIELD_RECEIPT, "PromiseYieldReceipt"),
        (BUFFERED_RECEIPT_INDICES, "BufferedReceiptIndices"),
        (BUFFERED_RECEIPT, "BufferedReceipt"),
        (BANDWIDTH_SCHEDULER_STATE, "BandwidthSchedulerState"),
        (BUFFERED_RECEIPT_GROUPS_QUEUE_DATA, "BufferedReceiptGroupsQueueData"),
        (BUFFERED_RECEIPT_GROUPS_QUEUE_ITEM, "BufferedReceiptGroupsQueueItem"),
        (GLOBAL_CONTRACT_CODE, "GlobalContractCode"),
        (GLOBAL_CONTRACT_NONCE, "GlobalContractNonce"),
        (PROMISE_YIELD_STATUS, "PromiseYieldStatus"),
        (YIELD_ID_TO_DATA_ID, "YieldIdToDataId"),
        (DATA_ID_TO_YIELD_ID, "DataIdToYieldId"),
    ];
```

**File:** runtime/runtime/src/global_contracts.rs (L93-105)
```rust
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
    account.set_storage_usage(
        account.storage_usage().checked_add(contract_identifier.len() as u64).ok_or_else(|| {
            StorageError::StorageInconsistentState(format!(
                "Storage usage integer overflow for account {}",
                account_id
            ))
        })?,
    );
    account.set_contract(contract);
```

**File:** runtime/runtime/src/global_contracts.rs (L149-168)
```rust
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
    // Increment the nonce and write it to state immediately to prevent multiple
    // distributions with the same nonce from being initiated. This requires
    // allowing the same nonce in the freshness check when applying the
    // distribution receipt.
    let nonce = increment_nonce(state_update, &id)?;
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
    let distribution_receipts =
        Receipt::new_global_contract_distribution(account_id, distribution_receipt);
    // No need to set receipt_id here, it will be generated as part of apply_action_receipt
    result.new_receipts.push(distribution_receipts);
    Ok(())
```

**File:** runtime/runtime/src/global_contracts.rs (L171-187)
```rust
/// Increments the nonce for the given global contract identifier and writes
/// it to state immediately.
fn increment_nonce(
    state_update: &mut TrieUpdate,
    id: &GlobalContractIdentifier,
) -> Result<u64, RuntimeError> {
    let identifier: GlobalContractCodeIdentifier = id.clone().into();

    let nonce_key = TrieKey::GlobalContractNonce { identifier };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;

    let new_nonce = stored_nonce.checked_add(1).ok_or_else(|| {
        RuntimeError::UnexpectedIntegerOverflow("increment_global_contract_nonce".into())
    })?;
    set_nonce(state_update, nonce_key, new_nonce);
    Ok(new_nonce)
}
```

**File:** runtime/runtime/src/global_contracts.rs (L189-232)
```rust
fn apply_distribution_current_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    state_update: &mut TrieUpdate,
) -> Result<Compute, RuntimeError> {
    let identifier = match &global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => GlobalContractCodeIdentifier::CodeHash(*hash),
        GlobalContractIdentifier::AccountId(account_id) => {
            GlobalContractCodeIdentifier::AccountId(account_id.clone())
        }
    };

    let is_nonce_fresh = check_and_update_nonce(global_contract_data, &identifier, state_update)?;
    if !is_nonce_fresh {
        return Ok(0);
    }

    let config = apply_state.config.wasm_config.clone();
    let trie_key = TrieKey::GlobalContractCode { identifier };
    let code_len = global_contract_data.code().len() as u64;
    state_update.set(trie_key, global_contract_data.code().to_vec());
    state_update.commit(StateChangeCause::ReceiptProcessing { receipt_hash: receipt.get_hash() });
    let code_hash = match global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => Some(*hash),
        GlobalContractIdentifier::AccountId(_) => None,
    };
    precompile_contract_with_warming(
        &ContractCode::new(global_contract_data.code().to_vec(), code_hash),
        config,
        apply_state.next_wasm_config.clone(),
        apply_state.cache.as_deref(),
    );
    near_vm_runner::report_metrics(apply_state.shard_id, "global_contract");
    let fees = &apply_state.config.fees;
    let per_byte_total = fees
        .deploy_global_contract_execution_per_byte
        .checked_mul(code_len)
        .ok_or(IntegerOverflowError)?;
    let compute = fees
        .deploy_global_contract_execution_base
        .checked_add(per_byte_total)
        .ok_or(IntegerOverflowError)?;
    Ok(compute)
```

**File:** runtime/runtime/src/contract_code.rs (L43-46)
```rust
        let local_hash = match GlobalContractIdentifier::try_from(account_contract) {
            Ok(gci) => {
                let code_hash = gci.clone().hash(state_update, access)?;
                return Ok(RuntimeContractIdentifier::Global { code_hash, identifier: gci });
```

**File:** runtime/runtime/src/actions.rs (L299-374)
```rust
pub(crate) fn action_delete_account(
    state_update: &mut TrieUpdate,
    account: &mut Option<Account>,
    actor_id: &mut AccountId,
    receipt: &Receipt,
    result: &mut ActionResult,
    account_id: &AccountId,
    delete_account: &DeleteAccountAction,
    config: &RuntimeConfig,
    current_protocol_version: ProtocolVersion,
) -> Result<(), StorageError> {
    let account_ref = account.as_ref().unwrap();
    let account_storage_usage = if ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
        .enabled(current_protocol_version)
    {
        let contract_storage = get_contract_storage_usage(state_update, account_id, account_ref)?;
        account_ref.storage_usage().saturating_sub(contract_storage)
    } else {
        // Legacy behavior: only subtracts local contract code, misses the
        // global contract identifier overhead.
        let account_storage_usage = account_ref.storage_usage();
        let code_len = get_code_len_or_default(
            state_update,
            account_id.clone(),
            account_ref.local_contract_hash().unwrap_or_default(),
        )?;
        debug_assert!(
            code_len == 0 || account_storage_usage > code_len,
            "account storage usage should be larger than code size. storage usage: {}, code size: {}",
            account_storage_usage,
            code_len
        );
        account_storage_usage.saturating_sub(code_len)
    };
    if account_storage_usage > Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE {
        result.result =
            Err(ActionErrorKind::DeleteAccountWithLargeState { account_id: account_id.clone() }
                .into());
        return Ok(());
    }
    let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
    if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: None,
            balance: gas_key_balance_to_burn,
        }
        .into());
        return Ok(());
    }
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_balance_to_burn).ok_or_else(|| {
            StorageError::StorageInconsistentState("tokens_burnt overflow".to_string())
        })?;
    if remove_result.gas_key_nonce_count > 0 {
        let compute = storage_removes_compute(
            &config.wasm_config.ext_costs,
            remove_result.gas_key_nonce_count,
            remove_result.gas_key_nonce_total_key_bytes,
            AccessKey::NONCE_VALUE_LEN * remove_result.gas_key_nonce_count,
        );
        result.compute_usage = safe_add_compute(result.compute_usage, compute).map_err(|_| {
            StorageError::StorageInconsistentState("compute_usage overflow".to_string())
        })?;
    }
    *actor_id = receipt.predecessor_id().clone();
    *account = None;
    Ok(())
```
