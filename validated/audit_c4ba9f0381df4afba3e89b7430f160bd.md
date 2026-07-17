### Title
Wrong Receiver Identity Passed Through `DelegateAction` Boundary in `validate_delegate_action` — (`File: runtime/runtime/src/action_validation.rs`)

---

### Summary

`validate_delegate_action` passes the **outer transaction's `receiver_id`** — which equals the meta-transaction sender account — as the `receiver` argument when validating inner `DeterministicStateInit` actions, instead of the `DelegateAction`'s own `receiver_id`. This is the exact nearcore analog of the factory/owner identity confusion: the intermediary (outer transaction) substitutes its own identity for the inner action's true target, breaking the receiver-derivation invariant and making legitimate `DeterministicStateInit` via meta-transaction impossible while allowing a crafted exploit transaction to pass initial validation.

---

### Finding Description

In `validate_delegate_action`, the `receiver` parameter is the outer transaction's `receiver_id` — the account that holds the `DelegateAction` receipt, i.e., `delegate_action.sender_id`. When the inner actions are validated, this outer receiver is forwarded to `validate_deterministic_state_init`, which checks:

```
derive_near_deterministic_account_id(state_init) == receiver_id
```

Pre-`FixDelegatedDeterministicStateInit`, `receiver_id` is the **outer tx receiver** (the meta-tx sender), not `delegate_action.receiver_id()` (the actual target of the inner action). The code itself documents this:

```rust
// runtime/runtime/src/action_validation.rs lines 188-199
let inner_receiver =
    if ProtocolFeature::FixDelegatedDeterministicStateInit.enabled(current_protocol_version) {
        // This is the correct receiver id to use for the check.
        delegate_action.receiver_id()
    } else {
        // This is a bug fixed with `FixDelegatedDeterministicStateInit` that
        // validated against the wrong id. This makes it impossible to
        // initialize deterministic accounts from meta transactions.
        // The bug cannot be abused, if someone crafts a state init that passes
        // validation here, it will fail when it is checked as incoming receipt.
        receiver
    };
``` [1](#0-0) 

The divergent values are exact:
- **Correct**: `delegate_action.receiver_id()` — the deterministic account being initialized
- **Actual (pre-fix)**: `receiver` — the outer tx receiver = `delegate_action.sender_id` (the meta-tx sender account)

The `validate_deterministic_state_init` function enforces:

```rust
let derived_id = derive_near_deterministic_account_id(&action.state_init);
if derived_id != *receiver_id {
    return Err(ActionsValidationError::InvalidDeterministicStateInitReceiver { ... });
}
``` [2](#0-1) 

With the wrong `receiver_id`, this check compares `derive(state_init)` against the meta-tx sender account, which is never equal to the derived deterministic ID, so all legitimate uses fail.

The exploit path (pre-fix): an attacker crafts a `DelegateAction` where `outer_tx.receiver_id = det_account_b = derive(state_init_b)` but `delegate_action.receiver_id = det_account_a` (wrong target). The old check validates `derive(state_init_b) == outer_tx.receiver_id = det_account_b` → passes tx validation. The receipt then fails at `validate_receipt` with `InvalidDeterministicStateInitReceiver`. The test `test_deterministic_state_init_meta_tx_receiver_check_pre_fix` confirms this exact path: [3](#0-2) 

The exploit construction: [4](#0-3) 

The `apply_delegate_action` function correctly sets `predecessor_id = sender_id` in the generated receipt, so the receipt-level check catches the mismatch. However, the tx-level validation invariant is broken: a transaction with a mismatched `DeterministicStateInit` receiver inside a `DelegateAction` passes the mempool admission check. [5](#0-4) 

---

### Impact Explanation

**Functional breakage (High)**: Any `DelegateAction` wrapping a `DeterministicStateInit` action is rejected at tx validation with `InvalidDeterministicStateInitReceiver` because the outer tx receiver is never the derived deterministic account ID. This makes the entire `DeterministicStateInit`-via-meta-transaction feature unusable for all protocol versions below 85.

**Broken tx-validation invariant (Medium)**: An attacker can submit a `DelegateAction` with a mismatched inner `DeterministicStateInit` receiver that passes mempool admission. The receipt-level check provides defense-in-depth, but the tx-level invariant — that only valid actions are admitted — is violated. This creates a discrepancy between what the network admits and what it executes.

The buggy code path remains present in the production binary for backward compatibility with protocol versions < 85 (minimum supported is 84). [6](#0-5) 

---

### Likelihood Explanation

Any unprivileged user can submit a `DelegateAction` (meta-transaction). The bug is triggered deterministically whenever a `DeterministicStateInit` action is wrapped inside a `DelegateAction`. No special access, validator role, or privileged position is required. The protocol feature `FixDelegatedDeterministicStateInit` is assigned to protocol version 85: [7](#0-6) 

Nodes processing receipts at protocol version 84 (the minimum supported) still execute the buggy path.

---

### Recommendation

Use `delegate_action.receiver_id()` — not the outer `receiver` — as the `inner_receiver` when validating inner actions of a `DelegateAction`. This is exactly what `ProtocolFeature::FixDelegatedDeterministicStateInit` implements. The fix should be applied unconditionally (removing the pre-fix branch) once the minimum supported protocol version is raised above 85, eliminating the dead buggy code path.

---

### Proof of Concept

**Legitimate use broken (pre-fix)**:
```
DelegateAction {
    sender_id: "user.near",
    receiver_id: "0sABCD..." (= derive(state_init)),
    actions: [DeterministicStateInit { state_init, ... }],
}
outer_tx.receiver_id = "user.near"

validate_deterministic_state_init checks:
  derive(state_init) == "user.near"  ← ALWAYS FALSE → InvalidDeterministicStateInitReceiver
```

**Exploit (pre-fix)**:
```
DelegateAction {
    sender_id: det_account_b,
    receiver_id: det_account_a,  ← wrong target
    actions: [DeterministicStateInit { state_init_b, ... }],
}
outer_tx.receiver_id = det_account_b = derive(state_init_b)

validate_deterministic_state_init checks:
  derive(state_init_b) == det_account_b  ← TRUE → tx admitted to mempool
  (receipt-level check then catches the mismatch)
``` [1](#0-0) [8](#0-7) [9](#0-8)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L180-208)
```rust
fn validate_delegate_action(
    limit_config: &LimitConfig,
    delegate_action: VersionedDelegateActionRef<'_>,
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    let actions = delegate_action.get_actions();
    let inner_receiver =
        if ProtocolFeature::FixDelegatedDeterministicStateInit.enabled(current_protocol_version) {
            // This is the correct receiver id to use for the check.
            delegate_action.receiver_id()
        } else {
            // This is a bug fixed with `FixDelegatedDeterministicStateInit` that
            // validated against the wrong id. This makes it impossible to
            // initialize deterministic accounts from meta transactions.
            // The bug cannot be abused, if someone crafts a state init that passes
            // validation here, it will fail when it is checked as incoming receipt.
            receiver
        };
    validate_actions_with_mode(
        limit_config,
        &actions,
        inner_receiver,
        current_protocol_version,
        mode,
    )?;
    Ok(())
}
```

**File:** runtime/runtime/src/action_validation.rs (L413-449)
```rust
fn validate_deterministic_state_init(
    limit_config: &LimitConfig,
    action: &DeterministicStateInitAction,
    receiver_id: &AccountId,
) -> Result<(), ActionsValidationError> {
    validate_global_contract_identifier(action.state_init.code())?;

    let derived_id = derive_near_deterministic_account_id(&action.state_init);

    if derived_id != *receiver_id {
        return Err(ActionsValidationError::InvalidDeterministicStateInitReceiver {
            derived_id,
            receiver_id: receiver_id.clone(),
        });
    }

    // State init entries must not violate limits of individual state keys and values.
    for (key, value) in action.state_init.data() {
        if key.len() as u64 > limit_config.max_length_storage_key {
            return Err(ActionsValidationError::DeterministicStateInitKeyLengthExceeded {
                length: key.len() as u64,
                limit: limit_config.max_length_storage_key,
            }
            .into());
        }

        if value.len() as u64 > limit_config.max_length_storage_value {
            return Err(ActionsValidationError::DeterministicStateInitValueLengthExceeded {
                length: value.len() as u64,
                limit: limit_config.max_length_storage_value,
            }
            .into());
        }
    }

    Ok(())
}
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L128-175)
```rust
/// Ensure there is no exploit with invalid deterministic account ids through
/// meta transactions.
///
/// With the old (buggy) code, `validate_delegate_action` used
/// `outer_tx.receiver_id` instead of `delegate_action.receiver_id` when
/// checking inner actions. The exploit tx therefore passes initial tx
/// validation. The exploit is prevented by a following `validate_receipt` check
/// when the meta transaction is unpacked.
#[test]
// Pins to a pre-spice protocol version; skipped under the spice feature.
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_deterministic_state_init_meta_tx_receiver_check_pre_fix() {
    let fix_version = ProtocolFeature::FixDelegatedDeterministicStateInit.protocol_version();
    let outcome = try_meta_tx_deterministic_receiver_exploit(fix_version - 1)
        .expect("without the fix, exploit tx passes initial tx validation");

    assert_matches!(
        outcome.status,
        FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
            kind: ActionErrorKind::NewReceiptValidationError(
                ReceiptValidationError::ActionsValidation(
                    ActionsValidationError::InvalidDeterministicStateInitReceiver { .. }
                )
            ),
            ..
        })),
        "expected InvalidDeterministicStateInitReceiver in NewReceiptValidationError, got: {:?}",
        outcome.status
    );
}

/// With `FixDelegatedDeterministicStateInit` in place, the exploit should
/// already be caught at the first tx validation.
#[test]
// Pins to a pre-spice protocol version; skipped under the spice feature.
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_deterministic_state_init_meta_tx_receiver_check() {
    let fix_version = ProtocolFeature::FixDelegatedDeterministicStateInit.protocol_version();
    let err = try_meta_tx_deterministic_receiver_exploit(fix_version)
        .expect_err("exploit tx must be rejected at tx validation with the fix");
    assert_matches!(
        err,
        InvalidTxError::ActionsValidation(
            ActionsValidationError::InvalidDeterministicStateInitReceiver { .. }
        ),
        "wrong error: {err:?}"
    );
}
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L236-265)
```rust
    // Craft the exploit: outer_tx.receiver = det_account_b = derive(state_init_b).
    // Old check: det_account_b == derive(state_init_b) passes.
    // The delegate action targets det_account_a, which is the wrong account.
    // In no protocol version can this ever be allowed to be executed successfully.
    let relayer = env.independent_account();
    let relayer_signer = create_user_test_signer(&relayer);
    let inner_action = Action::DeterministicStateInit(Box::new(DeterministicStateInitAction {
        state_init: state_init_b,
        deposit: Balance::ZERO,
    }));
    let delegate_nonce = env.next_nonce_for(&det_account_b);
    let delegate_action = DelegateAction {
        sender_id: det_account_b.clone(),
        receiver_id: det_account_a,
        actions: vec![NonDelegateAction::try_from(inner_action).unwrap()],
        nonce: delegate_nonce,
        max_block_height: 1_000_000,
        public_key: meta_tx_sender_signer.public_key(),
    };
    let signed_delegate_action =
        SignedDelegateAction::sign(&meta_tx_sender_signer, delegate_action);
    let tx = SignedTransaction::from_actions(
        env.next_nonce(),
        relayer,
        det_account_b,
        &relayer_signer,
        vec![Action::Delegate(Box::new(signed_delegate_action))],
        env.get_tx_block_hash(),
    );
    env.try_execute_tx(tx)
```

**File:** runtime/runtime/src/actions.rs (L455-469)
```rust
    // Generate a new receipt from DelegateAction.
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });
```

**File:** core/primitives-core/src/version.rs (L555-571)
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

**File:** core/primitives-core/src/version.rs (L596-598)
```rust
/// Minimum supported protocol version for the current binary
pub const MIN_SUPPORTED_PROTOCOL_VERSION: ProtocolVersion = 84;

```
