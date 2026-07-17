### Title
`remove_account()` leaves orphaned postponed-receipt and PromiseYield trie keys after `DeleteAccountAction` — (File: `core/store/src/utils/mod.rs`)

---

### Summary

`remove_account()`, the sole cleanup function called by `action_delete_account()`, removes `TrieKey::Account`, `TrieKey::ContractCode`, all `AccessKey`/`GasKeyNonce` entries, and all `ContractData` entries. It does **not** remove the postponed-receipt family (`PostponedReceipt`, `PendingDataCount`, `PostponedReceiptId`, `ReceivedData`) or the PromiseYield family (`PromiseYieldReceipt`, `PromiseYieldStatus`, `YieldIdToDataId`, `DataIdToYieldId`). These trie keys are keyed by `receiver_id` (the account being deleted) and remain in the state trie after the account record is gone, permanently orphaned unless the corresponding `DataReceipt`/`PromiseResume` eventually arrives.

---

### Finding Description

`remove_account()` is documented as "Removes account, code and all access keys and gas keys associated to it." [1](#0-0) 

It iterates only two prefixes — the access-key prefix and the contract-data prefix — and explicitly removes only those families: [2](#0-1) 

`action_delete_account()` calls `remove_account()` as its sole trie-cleanup step: [3](#0-2) 

The following trie key families are **never touched** by `remove_account()`:

| Trie key | Written by |
|---|---|
| `TrieKey::PostponedReceipt { receiver_id, receipt_id }` | `set_postponed_receipt()` |
| `TrieKey::PendingDataCount { receiver_id, receipt_id }` | `process_action_receipt()` |
| `TrieKey::PostponedReceiptId { receiver_id, data_id }` | `process_action_receipt()` |
| `TrieKey::ReceivedData { receiver_id, data_id }` | `set_received_data()` |
| `TrieKey::PromiseYieldReceipt { receiver_id, data_id }` | `set_promise_yield_receipt()` |
| `TrieKey::PromiseYieldStatus { receiver_id, data_id }` | `set_promise_yield_status()` |
| `TrieKey::YieldIdToDataId / DataIdToYieldId` | `set_yield_id_mapping()` | [4](#0-3) [5](#0-4) [6](#0-5) 

When `process_action_receipt()` stores a postponed receipt it writes `PostponedReceiptId`, `PendingDataCount`, and `PostponedReceipt` into the trie under the receiver's namespace: [7](#0-6) 

None of these writes update `account.storage_usage` through the normal account-storage accounting path; they are written directly via `set()` on the `TrieUpdate`. Consequently the `account_storage_usage > MAX_ACCOUNT_DELETION_STORAGE_USAGE` guard in `action_delete_account()` does not account for them, and the deletion succeeds even when these keys are present. [8](#0-7) 

**Concrete attack path:**

1. Attacker deploys contract B (controlled by attacker) and creates account A.
2. A calls B via a cross-contract call with `input_data_ids`, causing the runtime to write `PostponedReceipt`, `PendingDataCount`, and `PostponedReceiptId` under A's namespace.
3. Before B returns its `DataReceipt`, A submits a `DeleteAccountAction`. `remove_account()` removes `Account`, `ContractCode`, and `AccessKey` entries but leaves the three postponed-receipt keys intact.
4. A victim re-creates account A (NEAR account IDs are not permanently reserved after deletion).
5. Attacker instructs B to send the `DataReceipt`. The runtime finds `PostponedReceiptId`, decrements `PendingDataCount` to zero, fetches the old `PostponedReceipt`, and executes it against the **new** account A.
6. The old receipt's actions — which could include `DeployContract`, `AddKey`, or `DeleteAccount` — execute on the victim's account.

The same path applies to `PromiseYieldReceipt`: a contract calls `yield_create`, the account is deleted, the account is re-created, and the timeout mechanism fires a `PromiseResume` receipt that executes the old yield receipt on the new account. [9](#0-8) 

---

### Impact Explanation

**Orphaned trie state / permanent state bloat:** If the `DataReceipt` never arrives (e.g., B is abandoned), the orphaned keys remain in the state trie forever, inflating the shard's state root and storage costs with no way to reclaim them.

**Cross-account receipt injection on re-created accounts:** If the account ID is re-used, the old postponed receipt executes on the new account. Depending on the actions encoded in the original receipt, this can:
- Deploy arbitrary contract code to the victim's account (`DeployContract`).
- Add attacker-controlled access keys to the victim's account (`AddKey`).
- Delete the victim's account (`DeleteAccount`).

This breaks the protocol invariant that a newly created account starts with a clean, attacker-free trie namespace.

---

### Likelihood Explanation

The attacker must control both the calling account (A) and the callee (B). This is straightforward: both can be deployed by the same keypair. The only external dependency is that a victim re-creates the same account ID, which is plausible for short, desirable account names. The `DeleteAccountAction` is a standard, unprivileged protocol action available to any full-access key holder. No validator or privileged role is required.

---

### Recommendation

`remove_account()` should iterate and remove all trie key families keyed by `account_id` as receiver/owner, not only the access-key and contract-data prefixes. Specifically, add prefix-iteration removal for:

- `get_raw_prefix_for_received_data(account_id)` → removes `ReceivedData` and `PostponedReceiptId` entries.
- `get_raw_prefix_for_postponed_receipts(account_id)` → removes `PostponedReceipt` and `PendingDataCount` entries.
- `get_raw_prefix_for_promise_yield(account_id)` → removes `PromiseYieldReceipt`, `PromiseYieldStatus`, `YieldIdToDataId`, `DataIdToYieldId` entries.

Alternatively, add a pre-deletion guard in `action_delete_account()` that rejects deletion if any postponed-receipt or PromiseYield keys exist under the account, similar to the existing `DeleteAccountStaking` guard.

---

### Proof of Concept

```
1. Deploy contract B at "b.near" (attacker-controlled; B holds its DataReceipt until instructed).
2. Create account "victim.near" with a full-access key.
3. From "victim.near", call B with a cross-contract call that has one input_data_id.
   → Runtime writes:
       PostponedReceipt      { receiver_id: "victim.near", receipt_id: R }
       PendingDataCount      { receiver_id: "victim.near", receipt_id: R } = 1
       PostponedReceiptId    { receiver_id: "victim.near", data_id: D }   = R
4. Before B returns, submit DeleteAccountAction from "victim.near".
   → remove_account() removes Account, ContractCode, AccessKey entries.
   → PostponedReceipt / PendingDataCount / PostponedReceiptId remain in trie.
5. A new user creates "victim.near" (clean account, no knowledge of step 3).
6. Attacker instructs B to send DataReceipt { data_id: D, data: ... }.
   → Runtime finds PostponedReceiptId["victim.near", D] = R.
   → PendingDataCount reaches 0; runtime fetches PostponedReceipt R.
   → Runtime executes R's actions (e.g., AddKey with attacker's public key) on the new "victim.near".
   → Attacker now has a full-access key on the victim's account.
```

The root cause is in `remove_account()`: [10](#0-9) 

called unconditionally from `action_delete_account()`: [11](#0-10)

### Citations

**File:** core/store/src/utils/mod.rs (L74-117)
```rust
pub fn set_received_data(
    state_update: &mut TrieUpdate,
    receiver_id: AccountId,
    data_id: CryptoHash,
    data: &ReceivedData,
) {
    set(state_update, TrieKey::ReceivedData { receiver_id, data_id }, data);
}

pub fn get_received_data(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) -> Result<Option<ReceivedData>, StorageError> {
    get(trie, &TrieKey::ReceivedData { receiver_id: receiver_id.clone(), data_id })
}

pub fn has_received_data(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) -> Result<bool, StorageError> {
    trie.contains_key(
        &TrieKey::ReceivedData { receiver_id: receiver_id.clone(), data_id },
        AccessOptions::DEFAULT,
    )
}

pub fn set_postponed_receipt(state_update: &mut TrieUpdate, receipt: &Receipt) {
    assert!(matches!(receipt.versioned_receipt(), VersionedReceiptEnum::Action(_)));
    let key = TrieKey::PostponedReceipt {
        receiver_id: receipt.receiver_id().clone(),
        receipt_id: *receipt.receipt_id(),
    };
    set(state_update, key, receipt);
}

pub fn remove_postponed_receipt(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    receipt_id: CryptoHash,
) {
    state_update.remove(TrieKey::PostponedReceipt { receiver_id: receiver_id.clone(), receipt_id });
}
```

**File:** core/store/src/utils/mod.rs (L182-280)
```rust
pub fn set_promise_yield_receipt(state_update: &mut TrieUpdate, receipt: &Receipt) {
    match receipt.versioned_receipt() {
        VersionedReceiptEnum::PromiseYield(action_receipt) => {
            assert!(action_receipt.input_data_ids().len() == 1);
            let key = TrieKey::PromiseYieldReceipt {
                receiver_id: receipt.receiver_id().clone(),
                data_id: action_receipt.input_data_ids()[0],
            };
            set(state_update, key, receipt);
        }
        _ => unreachable!("Expected PromiseYield receipt"),
    }
}

pub fn remove_promise_yield_receipt(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) {
    state_update.remove(TrieKey::PromiseYieldReceipt { receiver_id: receiver_id.clone(), data_id });
}

pub fn get_promise_yield_receipt(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) -> Result<Option<Receipt>, StorageError> {
    get(trie, &TrieKey::PromiseYieldReceipt { receiver_id: receiver_id.clone(), data_id })
}

pub fn has_promise_yield_receipt(
    trie: &dyn TrieAccess,
    receiver_id: AccountId,
    data_id: CryptoHash,
) -> Result<bool, StorageError> {
    trie.contains_key(
        &TrieKey::PromiseYieldReceipt { receiver_id, data_id },
        AccessOptions::DEFAULT,
    )
}

pub fn get_promise_yield_status(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) -> Result<Option<PromiseYieldStatus>, StorageError> {
    get(trie, &TrieKey::PromiseYieldStatus { receiver_id: receiver_id.clone(), data_id })
}

pub fn has_promise_yield_status(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) -> Result<bool, StorageError> {
    trie.contains_key(
        &TrieKey::PromiseYieldStatus { receiver_id: receiver_id.clone(), data_id },
        AccessOptions::DEFAULT,
    )
}

pub fn set_promise_yield_status(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    data_id: CryptoHash,
    status: PromiseYieldStatus,
) {
    set(
        state_update,
        TrieKey::PromiseYieldStatus { receiver_id: receiver_id.clone(), data_id },
        &status,
    );
}

pub fn remove_promise_yield_status(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) {
    state_update.remove(TrieKey::PromiseYieldStatus { receiver_id: receiver_id.clone(), data_id });
}

pub fn set_yield_id_mapping(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    yield_id: YieldId,
    data_id: CryptoHash,
) {
    set(
        state_update,
        TrieKey::YieldIdToDataId { receiver_id: receiver_id.clone(), yield_id },
        &data_id,
    );
    set(
        state_update,
        TrieKey::DataIdToYieldId { receiver_id: receiver_id.clone(), data_id },
        &yield_id,
    );
}

```

**File:** core/store/src/utils/mod.rs (L486-556)
```rust
/// Removes account, code and all access keys and gas keys associated to it.
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

**File:** runtime/runtime/src/actions.rs (L311-338)
```rust
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
```

**File:** runtime/runtime/src/actions.rs (L356-374)
```rust
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

**File:** core/primitives/src/trie_key.rs (L203-219)
```rust
    /// purposes to avoid deserializing the entire receipt.
    PostponedReceiptId {
        receiver_id: AccountId,
        data_id: CryptoHash,
    } = col::POSTPONED_RECEIPT_ID,
    /// Used to store the number of still missing input data `u32` for a given receiver's
    /// `AccountId` and a given `receipt_id` of the receipt.
    PendingDataCount {
        receiver_id: AccountId,
        receipt_id: CryptoHash,
    } = col::PENDING_DATA_COUNT,
    /// Used to store the postponed receipt `primitives::receipt::Receipt` for a given receiver's
    /// `AccountId` and a given `receipt_id` of the receipt.
    PostponedReceipt {
        receiver_id: AccountId,
        receipt_id: CryptoHash,
    } = col::POSTPONED_RECEIPT,
```

**File:** runtime/runtime/src/lib.rs (L1416-1483)
```rust
            VersionedReceiptEnum::PromiseYield(_) => {
                // Received a new PromiseYield receipt. We simply store it and await
                // the corresponding PromiseResume receipt.
                set_promise_yield_receipt(state_update, receipt);
            }
            VersionedReceiptEnum::PromiseResume(data_receipt) => {
                if data_receipt.data.is_none() {
                    // This is a timeout resume. Check the status to see if the receipt has been resumed.
                    let status =
                        get_promise_yield_status(state_update, account_id, data_receipt.data_id)?;
                    if status == Some(PromiseYieldStatus::ResumeInitiated) {
                        // A non-timeout resume receipt has been sent, cancel the timeout.
                        return Ok(None);
                    }
                }

                // Received a new PromiseResume receipt delivering input data for a PromiseYield.
                // It is guaranteed that the PromiseYield has exactly one input data dependency
                // and that it arrives first, so we can simply find and execute it.
                if let Some(yield_receipt) =
                    get_promise_yield_receipt(state_update, account_id, data_receipt.data_id)?
                {
                    // Remove the receipt from the state
                    remove_promise_yield_receipt(state_update, account_id, data_receipt.data_id);

                    // Clear the PromiseYield status
                    remove_promise_yield_status(state_update, account_id, data_receipt.data_id);

                    // Clean up yield_id <-> data_id mappings if this was created by yield_create_with_id
                    if ProtocolFeature::YieldWithId.enabled(apply_state.current_protocol_version) {
                        if let Some(yield_id) = get_yield_id_for_data_id(
                            state_update,
                            account_id,
                            data_receipt.data_id,
                        )? {
                            remove_yield_id_mappings(
                                state_update,
                                account_id,
                                yield_id,
                                data_receipt.data_id,
                            );
                        }
                    }

                    // Save the data into the state keyed by the data_id
                    set_received_data(
                        state_update,
                        account_id.clone(),
                        data_receipt.data_id,
                        &ReceivedData { data: data_receipt.data.clone() },
                    );

                    // Execute the PromiseYield receipt. It will read the input data and clean it
                    // up from the state.
                    return self
                        .apply_action_receipt(
                            state_update,
                            apply_state,
                            pipeline_manager,
                            &yield_receipt,
                            receipt_sink,
                            instant_receipts,
                            validator_proposals,
                            stats,
                            epoch_info_provider,
                            receipt_to_tx,
                        )
                        .map(Some);
```

**File:** runtime/runtime/src/lib.rs (L1529-1576)
```rust
        let mut pending_data_count: u32 = 0;
        for data_id in action_receipt.input_data_ids() {
            if !has_received_data(state_update, account_id, *data_id)? {
                pending_data_count += 1;
                // The data for a given data_id is not available, so we save a link to this
                // receipt_id for the pending data_id into the state.
                set(
                    state_update,
                    TrieKey::PostponedReceiptId {
                        receiver_id: account_id.clone(),
                        data_id: *data_id,
                    },
                    receipt.receipt_id(),
                )
            }
        }

        if pending_data_count == 0 {
            // All input data is available. Executing the receipt. It will cleanup
            // input data from the state.
            return self
                .apply_action_receipt(
                    state_update,
                    apply_state,
                    pipeline_manager,
                    receipt,
                    receipt_sink,
                    instant_receipts,
                    validator_proposals,
                    stats,
                    epoch_info_provider,
                    receipt_to_tx,
                )
                .map(Some);
        } else {
            // Not all input data is available now.
            // Save the counter for the number of pending input data items into the state.
            set(
                state_update,
                TrieKey::PendingDataCount {
                    receiver_id: account_id.clone(),
                    receipt_id: *receipt.receipt_id(),
                },
                &pending_data_count,
            );
            // Save the receipt itself into the state.
            set_postponed_receipt(state_update, receipt);
        }
```
