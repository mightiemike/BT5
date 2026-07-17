Based on my investigation of the codebase, here is my analysis:

---

### Title
Unconditional `ReceiptEnum::ActionV2` (discriminant 5) creation without protocol-version gate causes Borsh deserialization failure on old-binary nodes during upgrade window — (`runtime/runtime/src/function_call.rs`)

### Summary

The new binary unconditionally emits `ReceiptEnum::ActionV2` (Borsh discriminant `5`) for every receipt produced by a function call, with no protocol-version guard. During the upgrade window — when new-binary nodes and old-binary nodes both run at the same protocol version (e.g., 84) — a new-binary node can write a postponed `ActionV2` receipt into the trie. An old-binary node that reads that trie entry calls `borsh::from_slice::<Receipt>` and encounters discriminant `5`, which it does not know, producing a `StorageError` / deserialization panic. This causes consensus divergence.

### Finding Description

**Borsh discriminant assignment:**

`ReceiptEnum` uses `#[borsh(use_discriminant = true)]` with explicit `#[repr(u8)]` values. `ActionV2` is assigned discriminant `5` and `PromiseYieldV2` discriminant `6`. Old binaries only know discriminants `0`–`4`. [1](#0-0) 

**Unconditional `ActionV2` creation — no protocol-version gate:**

In `action_function_call`, every receipt produced by a function call is unconditionally wrapped as `ReceiptEnum::ActionV2`. There is no `if protocol_version >= 85` guard anywhere in this path. [2](#0-1) 

**Postponed receipt storage path:**

When an `ActionV2` receipt has unresolved `input_data_ids`, `process_action_receipt` calls `set_postponed_receipt`, which Borsh-serializes the full `Receipt` (including the `ReceiptEnum::ActionV2` inner value with discriminant `5`) into `TrieKey::PostponedReceipt`. [3](#0-2) [4](#0-3) 

**Old-binary read path:**

When the completing `DataReceipt` arrives, `get_postponed_receipt` calls `borsh::from_slice::<Receipt>` on the stored bytes. An old binary whose `ReceiptEnum` only has variants `0`–`4` will fail on discriminant `5`. [5](#0-4) [6](#0-5) 

**Protocol version assignment:**

`promise_set_refund_to` and the `ActionV2` receipt type are introduced at protocol version 85. `MIN_SUPPORTED_PROTOCOL_VERSION` is 84, meaning the previous binary supports up to version 84 and does not know about `ActionV2`. [7](#0-6) [8](#0-7) 

**Attacker-controlled trigger path:**

An unprivileged user deploys a contract that calls `promise_batch_then` with at least one `output_data_receivers` entry (creating a receipt with `input_data_ids`), optionally also calling `promise_set_refund_to`. The new binary stores the resulting `ActionV2` receipt as a postponed receipt. No validator or admin privilege is required.

### Impact Explanation

During the upgrade window (new-binary nodes and old-binary nodes coexist at protocol version 84), a new-binary chunk producer writes a postponed `ReceiptEnum::ActionV2` receipt into the trie. When the completing data receipt arrives in a subsequent chunk, old-binary chunk validators call `get_postponed_receipt` → `borsh::from_slice::<Receipt>` → encounter discriminant `5` → return `StorageError::StorageInconsistentState` or panic. Old-binary nodes reject the chunk/block that new-binary nodes accept, causing consensus divergence.

### Likelihood Explanation

Any function call that creates a receipt with data dependencies (i.e., uses `promise_batch_then` with a data receiver) triggers this path. This is a common pattern. The divergence is deterministic and reproducible, not probabilistic.

### Recommendation

Gate the emission of `ReceiptEnum::ActionV2` behind a protocol-version check in `action_function_call`. When `protocol_version < 85`, fall back to `ReceiptEnum::Action` (dropping `refund_to` if it is `None`, or returning an error if it is `Some`). This mirrors the pattern used for other new receipt/action types (e.g., `PromiseYield`, `GlobalContractDistribution`).

### Proof of Concept

```rust
// Differential Borsh test
let receipt_v2 = ReceiptEnum::ActionV2(ActionReceiptV2 {
    signer_id: "alice.near".parse().unwrap(),
    refund_to: Some("bob.near".parse().unwrap()),
    signer_public_key: PublicKey::empty(KeyType::ED25519),
    gas_price: 0,
    output_data_receivers: vec![],
    input_data_ids: vec![],
    actions: vec![],
});
let bytes = borsh::to_vec(&receipt_v2).unwrap();
// bytes[0] == 5  (ActionV2 discriminant)

// Simulate old binary that only knows discriminants 0-4:
#[derive(BorshDeserialize)]
#[borsh(use_discriminant = true)]
#[repr(u8)]
enum OldReceiptEnum {
    Action(ActionReceipt) = 0,
    Data(DataReceipt) = 1,
    PromiseYield(ActionReceipt) = 2,
    PromiseResume(DataReceipt) = 3,
    GlobalContractDistribution(GlobalContractDistributionReceipt) = 4,
}
let result = OldReceiptEnum::try_from_slice(&bytes);
assert!(result.is_err()); // discriminant 5 is unknown → deserialization error
```

This error, returned from `get_postponed_receipt` as `StorageError`, causes old-binary nodes to reject the chunk, diverging from new-binary nodes.

### Citations

**File:** core/primitives/src/receipt.rs (L563-574)
```rust
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
#[borsh(use_discriminant = true)]
#[repr(u8)]
pub enum ReceiptEnum {
    Action(ActionReceipt) = 0,
    Data(DataReceipt) = 1,
    PromiseYield(ActionReceipt) = 2,
    PromiseResume(DataReceipt) = 3,
    GlobalContractDistribution(GlobalContractDistributionReceipt) = 4,
    ActionV2(ActionReceiptV2) = 5,
    PromiseYieldV2(ActionReceiptV2) = 6,
}
```

**File:** runtime/runtime/src/function_call.rs (L172-185)
```rust
                let new_action_receipt = ActionReceiptV2 {
                    signer_id: action_receipt.signer_id().clone(),
                    signer_public_key: action_receipt.signer_public_key().clone(),
                    refund_to: receipt.refund_to,
                    gas_price: action_receipt.gas_price(),
                    output_data_receivers: receipt.output_data_receivers,
                    input_data_ids: receipt.input_data_ids,
                    actions: receipt.actions,
                };
                let new_receipt = if receipt.is_promise_yield {
                    ReceiptEnum::PromiseYieldV2(new_action_receipt)
                } else {
                    ReceiptEnum::ActionV2(new_action_receipt)
                };
```

**File:** runtime/runtime/src/lib.rs (L1352-1358)
```rust
                        let ready_receipt =
                            get_postponed_receipt(state_update, account_id, receipt_id)?
                                .ok_or_else(|| {
                                    StorageError::StorageInconsistentState(
                                        "pending receipt should be in the state".to_string(),
                                    )
                                })?;
```

**File:** runtime/runtime/src/lib.rs (L1563-1576)
```rust
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

**File:** core/store/src/utils/mod.rs (L102-109)
```rust
pub fn set_postponed_receipt(state_update: &mut TrieUpdate, receipt: &Receipt) {
    assert!(matches!(receipt.versioned_receipt(), VersionedReceiptEnum::Action(_)));
    let key = TrieKey::PostponedReceipt {
        receiver_id: receipt.receiver_id().clone(),
        receipt_id: *receipt.receipt_id(),
    };
    set(state_update, key, receipt);
}
```

**File:** core/store/src/utils/mod.rs (L119-125)
```rust
pub fn get_postponed_receipt(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    receipt_id: CryptoHash,
) -> Result<Option<Receipt>, StorageError> {
    get(trie, &TrieKey::PostponedReceipt { receiver_id: receiver_id.clone(), receipt_id })
}
```

**File:** core/primitives-core/src/version.rs (L555-572)
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

```

**File:** core/primitives/src/version.rs (L597-597)
```rust

```
