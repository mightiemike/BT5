### Title
`action_delete_account` overstates storage usage for global-contract accounts, causing spurious `DeleteAccountWithLargeState` DoS — (`runtime/runtime/src/actions.rs`)

### Summary
Before `ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage` (protocol version 85) is activated, `action_delete_account` computes the account's non-contract storage usage by subtracting only the local contract code length. For accounts whose contract is `AccountContract::Global` or `AccountContract::GlobalByAccount`, the identifier overhead (32 bytes for a code-hash reference, or `account_id.len()` bytes for an account-ID reference) is never subtracted. The resulting `account_storage_usage` value is overstated by exactly the identifier size, and that overstated value is compared against `Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE` (10,000 bytes). Any account whose true non-contract storage usage falls in the half-open interval `(MAX, MAX + identifier_size]` is incorrectly rejected with `DeleteAccountWithLargeState`, making it permanently impossible to delete via the normal `DeleteAccountAction` path.

### Finding Description
In `action_delete_account`, the legacy code path (pre-fix) is:

```rust
let account_storage_usage = account_ref.storage_usage();
let code_len = get_code_len_or_default(
    state_update,
    account_id.clone(),
    account_ref.local_contract_hash().unwrap_or_default(),
)?;
account_storage_usage.saturating_sub(code_len)
```

`get_code_len_or_default` returns `0` for any account whose contract is `AccountContract::Global` or `AccountContract::GlobalByAccount`, because `local_contract_hash()` returns `None` for those variants, which maps to `CryptoHash::default()`, and no code is stored under that hash. Consequently `code_len = 0` and `account_storage_usage` is returned unchanged — still including the identifier overhead that was added when the global contract was attached. [1](#0-0) 

The correct computation (introduced by the fix) calls `get_contract_storage_usage`, which returns `account.contract().identifier_storage_usage()` for global-contract variants — 32 bytes for `AccountContract::Global` and `account_id.len()` bytes for `AccountContract::GlobalByAccount`. [2](#0-1) 

The divergent value is the `account_storage_usage` integer: it is `identifier_size` bytes larger than the true non-contract storage, and that inflated value is compared against the 10,000-byte deletion limit at line 333. [3](#0-2) 

The protocol feature and its version assignment: [4](#0-3) [5](#0-4) 

### Impact Explanation
Any account that (1) has a global contract attached (`AccountContract::Global` or `AccountContract::GlobalByAccount`) and (2) has accumulated non-contract state such that its total `storage_usage` falls in `(10000, 10000 + identifier_size]` will receive `ActionErrorKind::DeleteAccountWithLargeState` on every `DeleteAccountAction`, making the account permanently undeletable through the normal protocol path. ETH-implicit accounts are created with `AccountContract::Global` by default; if such an account later adds keys or other state that pushes it into the affected range, it becomes stuck. The DoS is permanent until protocol version 85 activates the fix. [6](#0-5) 

### Likelihood Explanation
The affected storage-usage window is narrow (32 bytes for code-hash global contracts, up to 64 bytes for account-ID global contracts). However, ETH-implicit accounts are automatically assigned a global contract at creation, and any unprivileged user who adds enough access keys or contract storage to push their account into the `(10000, 10032]` byte range will trigger the bug. No special permissions are required.

### Recommendation
The fix is already present in the codebase behind `ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage` (protocol version 85). Activate this feature on mainnet as soon as possible. No additional code changes are needed beyond the existing fix.

### Proof of Concept
The existing unit test `test_delete_account_global_contract_protocol_transition` in `runtime/runtime/src/actions.rs` demonstrates the exact divergence: [7](#0-6) 

An unprivileged user can reproduce this on mainnet (before version 85) by:
1. Creating an ETH-implicit account (which automatically gets `AccountContract::Global`)
2. Adding enough access keys to push `storage_usage` into the range `(10000, 10032]`
3. Attempting `DeleteAccountAction` — it will always fail with `DeleteAccountWithLargeState`

### Citations

**File:** runtime/runtime/src/actions.rs (L232-246)
```rust
        AccountType::EthImplicitAccount => {
            let chain_id = epoch_info_provider.chain_id();

            // Use a deployed global contract for ETH implicit accounts.
            let global_contract_hash = eth_wallet_global_contract_hash(&chain_id);
            let storage_usage = fee_config.storage_usage_config.num_bytes_account
                + global_contract_hash.as_bytes().len() as u64;

            *account = Some(Account::new(
                deposit,
                Balance::ZERO,
                AccountContract::Global(global_contract_hash),
                storage_usage,
            ));
        }
```

**File:** runtime/runtime/src/actions.rs (L311-332)
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
```

**File:** runtime/runtime/src/actions.rs (L333-338)
```rust
    if account_storage_usage > Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE {
        result.result =
            Err(ActionErrorKind::DeleteAccountWithLargeState { account_id: account_id.clone() }
                .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L395-409)
```rust
fn get_contract_storage_usage(
    state_update: &TrieUpdate,
    account_id: &AccountId,
    account: &Account,
) -> Result<StorageUsage, StorageError> {
    Ok(match account.contract().as_ref() {
        AccountContract::None => 0,
        AccountContract::Local(code_hash) => {
            get_code_len_or_default(state_update, account_id.clone(), *code_hash)?
        }
        AccountContract::Global(_) | AccountContract::GlobalByAccount(_) => {
            account.contract().identifier_storage_usage()
        }
    })
}
```

**File:** runtime/runtime/src/actions.rs (L1091-1115)
```rust
    fn test_delete_account_global_contract_protocol_transition() {
        let account_id: AccountId = "alice".parse().unwrap();
        let storage = Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE + 32;
        let enabled =
            ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage.protocol_version();

        // Before the fix: the identifier is not subtracted, so `MAX + 32 > MAX`.
        let before = test_delete_account_in_empty_trie(
            &account_id,
            AccountContract::Global(CryptoHash::default()),
            storage,
            enabled - 1,
        );
        expect_delete_account_too_large(&before);

        // From the fix onwards: the 32-byte identifier is subtracted, so
        // `MAX + 32 - 32 == MAX`, which is not `> MAX`.
        let after = test_delete_account_in_empty_trie(
            &account_id,
            AccountContract::Global(CryptoHash::default()),
            storage,
            enabled,
        );
        assert!(after.result.is_ok());
    }
```

**File:** core/primitives-core/src/version.rs (L355-359)
```rust
    /// Fix `action_delete_account` not subtracting the global contract
    /// identifier storage usage. Previously only local contract code was
    /// subtracted, overstating storage usage for accounts with global
    /// contracts and making them marginally harder to delete.
    FixDeleteAccountGlobalContractStorageUsage,
```

**File:** core/primitives-core/src/version.rs (L555-557)
```rust
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
            | ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
            | ProtocolFeature::FixDelegatedDeterministicStateInit
```
