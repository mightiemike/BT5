### Title
ML-DSA-65 Gas-Key Fee Divergence: Host-Function Path Uses Borsh-Encoded Length Instead of Trie-ID Length - (`File: runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

The `gas_key_transfer_send_fee` and `gas_key_transfer_exec_fee` helpers accept a `public_key_len: usize` parameter that is supposed to represent the on-trie key identifier length. The **transaction path** correctly supplies `action.public_key.trie_id_len()` (33 bytes for ML-DSA-65), but the **host-function path** supplies the raw `public_key_len as usize` received from the contract — which is the borsh-encoded wire length (1953 bytes for ML-DSA-65). For ED25519 and SECP256K1 the two lengths are identical, so the divergence is invisible until an ML-DSA-65 gas key is used. Once `PostQuantumSignatures` (protocol version 85, now stable) is active, any contract calling `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_withdraw_from_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, or `promise_batch_action_add_gas_key_with_function_call` with an ML-DSA-65 key is charged ~59× more gas per byte than the equivalent transaction, breaking the fee-parity invariant and potentially causing out-of-gas failures.

---

### Finding Description

**Correct (transaction) path** — `runtime/runtime/src/config.rs`:

```rust
TransferToGasKey(action) => {
    gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
        .total()
}
WithdrawFromGasKey(action) => {
    gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
        .total()
}
``` [1](#0-0) 

For ML-DSA-65, `trie_id_len()` = 33 (1 tag byte + 32-byte SHA3-256 hash).

**Divergent (host-function) path** — `runtime/near-vm-runner/src/logic/logic.rs`:

```rust
let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← raw borsh-encoded length from contract memory
);
``` [2](#0-1) 

And identically in the wasmtime runner: [3](#0-2) 

For ML-DSA-65, `public_key_len` is 1953 (1 tag byte + 1952-byte raw pubkey), the borsh-encoded wire length.

The same divergence exists for `gas_key_add_key_exec_fee` in the `AddKey` host-function path: [4](#0-3) 

vs. the transaction path which correctly passes `public_key.trie_id_len()`: [5](#0-4) 

The fee helpers themselves: [6](#0-5) 

`gas_key_transfer_exec_fee` internally calls `access_key_key_len(account_id_len, public_key_len)` to compute the trie key length. When `public_key_len` is 1953 instead of 33, the computed trie key length is inflated by 1920 bytes, making the exec-fee per-byte component ~59× too large.

The fee-parity integration test only exercises ED25519 keys (where `trie_id_len()` == borsh `len()` == 33), so the divergence is not caught: [7](#0-6) 

The `trie_id_len` / `len` split is explicitly documented as a new contract that all fee paths must respect: [8](#0-7) 

---

### Impact Explanation

For every `TransferToGasKey`, `WithdrawFromGasKey`, or gas-key `AddKey` action appended via a host function with an ML-DSA-65 public key:

- **Send fee per-byte component**: charged at `gas_key_byte × 1953` instead of `gas_key_byte × 33` — a ~59× overcharge.
- **Exec fee per-byte component**: `access_key_key_len` receives 1953 instead of 33, inflating the trie-key-length estimate by 1920 bytes and overcharging the exec fee proportionally.

A contract that budgets gas based on the transaction-path cost (33 bytes) will run out of gas when the same operation is performed via the host-function path. This breaks the fee-parity invariant that the existing test suite asserts for ED25519 keys, and can cause silent out-of-gas failures for any contract that manages ML-DSA-65 gas keys programmatically.

---

### Likelihood Explanation

`PostQuantumSignatures` is assigned protocol version 85 and `STABLE_PROTOCOL_VERSION` is 86, so the feature is active on mainnet. [9](#0-8) 

Any unprivileged user can deploy a contract that calls `promise_batch_action_transfer_to_gas_key` with an ML-DSA-65 public key. The divergence is triggered automatically by the host-function dispatch; no privileged role is required.

---

### Recommendation

In all four host-function call sites (`promise_batch_action_transfer_to_gas_key`, `promise_batch_action_withdraw_from_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`), replace the raw `public_key_len as usize` argument to `gas_key_transfer_send_fee`, `gas_key_transfer_exec_fee`, and `gas_key_add_key_exec_fee` with `public_key.decode()?.trie_id_len()` (after the key has been decoded and validated). This mirrors the transaction path and ensures the per-byte fee is computed against the 33-byte on-trie identifier rather than the 1953-byte borsh-encoded wire form.

Additionally, extend the fee-parity test (`test_gas_key_fee_parity`) to cover ML-DSA-65 keys so the invariant is enforced for all key types.

---

### Proof of Concept

Exact divergent values for an ML-DSA-65 key:

| Path | `public_key_len` passed | `gas_key_byte` multiplier |
|---|---|---|
| Transaction (`trie_id_len()`) | **33** | 33 |
| Host function (`public_key_len as usize`) | **1953** | 1953 |

Ratio: 1953 / 33 = **59.18×** overcharge on the per-byte component.

A contract calling `promise_batch_action_transfer_to_gas_key` with an ML-DSA-65 key and budgeting `gas_key_byte × 33` gas will receive an out-of-gas error because the runtime charges `gas_key_byte × 1953`. The same action submitted as a `TransferToGasKey` transaction succeeds with the budgeted amount. [10](#0-9)

### Citations

**File:** runtime/runtime/src/config.rs (L114-194)
```rust
            TransferToGasKey(action) => {
                gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
                    .total()
            }
            Stake(_) => fees.fee(ActionCosts::stake).send_fee(sender_is_receiver),
            AddKey(add_key_action) => permission_send_fees(
                &add_key_action.access_key.permission,
                fees,
                sender_is_receiver,
            ),
            DeleteKey(_) => fees.fee(ActionCosts::delete_key).send_fee(sender_is_receiver),
            DeleteAccount(_) => fees.fee(ActionCosts::delete_account).send_fee(sender_is_receiver),
            Delegate(signed_delegate_action) => {
                let delegate_cost = fees.fee(ActionCosts::delegate).send_fee(sender_is_receiver);
                let delegate_action = &signed_delegate_action.delegate_action;

                delegate_cost
                    .checked_add(total_send_fees(
                        config,
                        sender_is_receiver,
                        &delegate_action.get_actions(),
                        &delegate_action.receiver_id,
                    )?)
                    .unwrap()
            }
            DelegateV2(signed_delegate_action) => {
                let delegate_cost = fees.fee(ActionCosts::delegate).send_fee(sender_is_receiver);
                let delegate_action =
                    VersionedDelegateActionRef::from(&signed_delegate_action.delegate_action);

                delegate_cost
                    .checked_add(total_send_fees(
                        config,
                        sender_is_receiver,
                        &delegate_action.get_actions(),
                        delegate_action.receiver_id(),
                    )?)
                    .unwrap()
            }
            DeployGlobalContract(DeployGlobalContractAction { code, .. }) => {
                let num_bytes = code.len() as u64;

                let base_fee =
                    fees.fee(ActionCosts::deploy_global_contract_base).send_fee(sender_is_receiver);
                let byte_fee =
                    fees.fee(ActionCosts::deploy_global_contract_byte).send_fee(sender_is_receiver);
                let all_bytes_fee = byte_fee.checked_mul(num_bytes).unwrap();

                base_fee.checked_add(all_bytes_fee).unwrap()
            }
            UseGlobalContract(action) => {
                let num_bytes = action.contract_identifier.len() as u64;
                let base_fee =
                    fees.fee(ActionCosts::use_global_contract_base).send_fee(sender_is_receiver);
                let byte_fee =
                    fees.fee(ActionCosts::use_global_contract_byte).send_fee(sender_is_receiver);
                let all_bytes_fee = byte_fee.checked_mul(num_bytes).unwrap();

                base_fee.checked_add(all_bytes_fee).unwrap()
            }
            DeterministicStateInit(action) => {
                let num_entries = action.state_init.data().len() as u64;
                let num_bytes = action.state_init.len_bytes();
                let base_fee = fees
                    .fee(ActionCosts::deterministic_state_init_base)
                    .send_fee(sender_is_receiver);
                let entry_fee = fees
                    .fee(ActionCosts::deterministic_state_init_entry)
                    .send_fee(sender_is_receiver);
                let all_entries_fee = entry_fee.checked_mul(num_entries).unwrap();
                let byte_fee = fees
                    .fee(ActionCosts::deterministic_state_init_byte)
                    .send_fee(sender_is_receiver);
                let all_bytes_fee = byte_fee.checked_mul(num_bytes as u64).unwrap();

                base_fee.checked_add(all_bytes_fee).unwrap().checked_add(all_entries_fee).unwrap()
            }
            WithdrawFromGasKey(action) => {
                gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
                    .total()
            }
```

**File:** runtime/runtime/src/config.rs (L389-395)
```rust
    let nonce_fee = gas_key_add_key_exec_fee(
        fees,
        account_id.len(),
        public_key.trie_id_len(),
        gas_key_info.num_nonces,
    );
    key_fee.checked_add(nonce_fee.total()).unwrap()
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3091-3096)
```rust
        let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
        let exec = gas_key_transfer_exec_fee(
            &self.fees_config,
            receiver_id.len(),
            public_key_len as usize,
        );
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3323-3325)
```rust
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
    let exec =
        gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3395-3403)
```rust
    let exec_fee = gas_key_add_key_exec_fee(
        &ctx.fees_config,
        receiver_id.len(),
        public_key_len as usize,
        num_nonces,
    );
    ctx.result_state.gas_counter.pay_gas_key_add_key_fees(send_fee, &exec_fee)?;
    ctx.ext.append_action_add_gas_key_with_full_access(
        receipt_idx,
```

**File:** core/parameters/src/cost.rs (L816-847)
```rust
pub fn gas_key_transfer_send_fee(
    cfg: &RuntimeFeesConfig,
    sender_is_receiver: bool,
    public_key_len: usize,
) -> GasKeyTransferFee {
    let base = cfg.fee(ActionCosts::gas_key_transfer_base).send_fee(sender_is_receiver);
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .send_fee(sender_is_receiver)
        .checked_mul(public_key_len as u64)
        .unwrap();
    GasKeyTransferFee { base, per_byte }
}

/// Exec fee for TransferToGasKey / WithdrawFromGasKey actions.
/// Based on the access key trie key length + estimated value length (what the
/// receiver needs to read/write in the trie).
pub fn gas_key_transfer_exec_fee(
    cfg: &RuntimeFeesConfig,
    account_id_len: usize,
    public_key_len: usize,
) -> GasKeyTransferFee {
    let base = cfg.fee(ActionCosts::gas_key_transfer_base).exec_fee();
    let trie_key_len = access_key_key_len(account_id_len, public_key_len);
    let estimated_value_len = AccessKey::min_gas_key_borsh_len();
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .exec_fee()
        .checked_mul((trie_key_len + estimated_value_len) as u64)
        .unwrap();
    GasKeyTransferFee { base, per_byte }
}
```

**File:** test-loop-tests/src/tests/gas_keys.rs (L1037-1061)
```rust
fn test_gas_key_fee_parity(mode: GasKeyKind) {
    let mut setup = setup_host_function_test();
    let account = setup.account.clone();

    let num_nonces: NonceIndex = 4;
    let gas_key_a_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_tx").into();
    let gas_key_b_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_host_fn").into();

    let public_key_b_base64 = near_primitives_core::serialize::to_base64(
        &borsh::to_vec(&gas_key_b_signer.public_key()).unwrap(),
    );

    // Add gas key A via transaction, B via host function
    let add_a_outcome = setup.run_actions(vec![Action::AddKey(Box::new(AddKeyAction {
        public_key: gas_key_a_signer.public_key(),
        access_key: mode.access_key(num_nonces, &account),
    }))]);
    let add_b_outcome = setup.run_call_promise(serde_json::json!([
        {"batch_create": {"account_id": account.as_str()}, "id": 0},
        mode.add_action_json(num_nonces, &account, &public_key_b_base64),
    ]));
    assert_eq!(add_a_outcome.gas_burnt, add_b_outcome.gas_burnt);
    assert_eq!(add_a_outcome.tokens_burnt, add_b_outcome.tokens_burnt);
```

**File:** docs/architecture/how/post_quantum_signatures.md (L189-191)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.
```

**File:** core/primitives-core/src/version.rs (L555-573)
```rust
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
            | ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
            | ProtocolFeature::FixDelegatedDeterministicStateInit
            | ProtocolFeature::GasKeys
            | ProtocolFeature::ContinuousEpochSync
            | ProtocolFeature::DynamicResharding
            | ProtocolFeature::StickyReshardingValidatorAssignment
            | ProtocolFeature::StrictNonce
            | ProtocolFeature::PostQuantumSignatures
            | ProtocolFeature::UniqueChunkTransactions
            | ProtocolFeature::ValidateBlockOrdinalAndEpochSyncDataHash
            | ProtocolFeature::YieldWithId
            | ProtocolFeature::ExecutionMetadataV4
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,

            ProtocolFeature::FixContractLoadingError => 86,
```

**File:** runtime/runtime/src/access_keys.rs (L1232-1238)
```rust
    fn test_ml_dsa_65_trie_id_len_is_hash_size() {
        let pq_pk: PublicKey =
            near_crypto::SecretKey::from_seed(near_crypto::KeyType::MLDSA65, "trie-id-len")
                .public_key();
        assert_eq!(pq_pk.trie_id_len(), 1 + 32);
        assert_eq!(pq_pk.len(), 1 + 1952); // borsh form still reports full
    }
```
