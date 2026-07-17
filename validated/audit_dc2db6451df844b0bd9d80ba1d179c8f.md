### Title
Unprivileged Attacker Can Front-Run `SignedDelegateAction` to Permanently Invalidate a Relayer's Meta-Transaction - (`File: runtime/runtime/src/actions.rs`)

### Summary

Any unprivileged network participant can extract a `SignedDelegateAction` from a pending relayer transaction visible in the mempool, wrap it in their own outer transaction, and submit it to the same shard. If the attacker's transaction is included first, the access-key nonce is consumed, and the relayer's original transaction fails with `DelegateActionInvalidNonce`. The signed credential cannot be reused; the user must re-sign with a new nonce. This is a griefing attack with no profit motive but direct damage to relayer services and meta-transaction users.

### Finding Description

NEAR's meta-transaction system (NEP-366) allows a user (Alice) to sign a `DelegateAction` off-chain and hand it to a relayer. The relayer wraps it in an outer `SignedTransaction` and submits it on-chain. The `DelegateAction` carries a `nonce` field that must be strictly greater than the current `access_key.nonce` for Alice's signing key. [1](#0-0) 

The nonce is validated and consumed in `validate_delegate_action_key`: [2](#0-1) 

The outer transaction's `receiver_id` must equal `delegate_action.sender_id` (Alice's account), but the outer transaction's `signer_id` can be **any** account. There is no binding between the outer signer and the `SignedDelegateAction` contents.

Attack path:
1. Alice signs `DelegateAction { nonce: N, sender_id: alice, ... }` and sends it off-chain to a relayer.
2. The relayer broadcasts `SignedTransaction { signer: relayer, receiver: alice, actions: [Delegate(signed_da)] }` to the network.
3. An attacker observing the mempool extracts the `SignedDelegateAction` (which is fully valid — it carries Alice's signature).
4. The attacker constructs `SignedTransaction { signer: attacker, receiver: alice, actions: [Delegate(signed_da)] }` — a perfectly valid transaction.
5. If the attacker's transaction is included in a chunk before the relayer's, `validate_delegate_action_key` advances Alice's nonce to `N`.
6. The relayer's transaction then fails: `DelegateActionInvalidNonce { delegate_nonce: N, ak_nonce: N }`.

The `SignedDelegateAction` is a one-time-use signed credential. Once consumed by the attacker, Alice must re-sign with nonce `N+1`. The relayer has already paid gas for the failed outer transaction. [3](#0-2) 

The feature is stabilized at protocol version 85: [4](#0-3) 

### Impact Explanation

- **Relayer griefing**: The relayer's gas is burned on a transaction that produces `DelegateActionInvalidNonce`. The relayer must detect the failure, contact Alice for a new signature, and resubmit — or abandon the service request.
- **User denial of service**: A targeted attacker can repeatedly front-run every `SignedDelegateAction` Alice submits to any relayer, permanently preventing her from using meta-transactions without the attacker also spending gas.
- **Relayer service disruption**: A public relayer service (e.g., a gasless onboarding service) can be systematically disrupted at low cost to the attacker.

The `DelegateV2` variant (gas-key nonces) is equally affected because `validate_delegate_action_key` handles both `TransactionNonce::Nonce` and `TransactionNonce::GasKeyNonce` paths with the same monotonic check: [5](#0-4) 

### Likelihood Explanation

- Any full node or RPC node can observe pending transactions in the gossip layer before they are included in a chunk.
- The attacker's transaction is structurally identical to the relayer's (same `receiver_id`, same `actions` payload) and passes all validation checks.
- No special privilege is required — only the ability to submit transactions (any funded account).
- The attack costs the attacker gas for the outer transaction, but the attacker's `DelegateAction` execution succeeds (Alice's intended action executes), so the attacker may even benefit from executing Alice's action on their behalf.
- Chunk producers order transactions within a chunk; a malicious chunk producer can guarantee ordering.

### Recommendation

Bind the `SignedDelegateAction` to a specific authorized relayer by including the relayer's `account_id` or `public_key` in the signed payload of `DelegateAction`. The runtime should verify that the outer transaction's `signer_id` matches the bound relayer field before accepting the delegate action. Alternatively, introduce a `relayer_id: Option<AccountId>` field in `DelegateAction`; when set, `validate_delegate_action_key` should reject any outer transaction whose `signer_id` does not match. [6](#0-5) 

### Proof of Concept

```
// Alice signs a DelegateAction with nonce N and sends it to a relayer off-chain.
// The relayer broadcasts:
//   SignedTransaction { signer: relayer, receiver: alice, actions: [Delegate(signed_da)] }
//
// Attacker observes the mempool, extracts signed_da, and submits:
//   SignedTransaction { signer: attacker, receiver: alice, actions: [Delegate(signed_da)] }
//
// If attacker's tx is included first:
//   - validate_delegate_action_key runs, nonce N > current_nonce passes
//   - Alice's access_key.nonce is set to N
//   - Attacker's tx succeeds (Alice's action executes under attacker's outer tx)
//
// Relayer's tx is then processed:
//   - validate_delegate_action_key: delegate_nonce N <= current_nonce N
//   - Returns DelegateActionInvalidNonce { delegate_nonce: N, ak_nonce: N }
//   - Relayer's gas is burned; Alice must re-sign with nonce N+1
```

The exact error variant produced is `ActionErrorKind::DelegateActionInvalidNonce` at: [2](#0-1) 

confirmed by the existing test `test_validate_delegate_action_key_update_nonce`: [7](#0-6)

### Citations

**File:** core/primitives/src/action/delegate.rs (L46-64)
```rust
pub struct DelegateAction {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    ///
    /// With the meta transactions MVP defined in NEP-366, nested
    /// DelegateActions are not allowed. A separate type is used to enforce it.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce to ensure that the same delegate action is not sent twice by a
    /// relayer and should match for given account's `public_key`.
    /// After this action is processed it will increment.
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

**File:** runtime/runtime/src/actions.rs (L535-556)
```rust
fn validate_delegate_action_key(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    delegate_action: VersionedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let sender_id = delegate_action.sender_id();
    let public_key = delegate_action.public_key();
    // 'sender_id' account existence must be checked by a caller
    let mut access_key = match get_access_key(state_update, sender_id, public_key)? {
        Some(access_key) => access_key,
        None => {
            result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::AccessKeyNotFound {
                    account_id: sender_id.clone(),
                    public_key: public_key.clone().into(),
                },
            )
            .into());
            return Ok(());
        }
    };
```

**File:** runtime/runtime/src/actions.rs (L561-601)
```rust
    let delegate_nonce = delegate_action.nonce();
    let (current_nonce, nonce_update) = match delegate_nonce {
        TransactionNonce::Nonce { .. } => {
            if access_key.gas_key_info().is_some() {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DelegateActionRequiresNonGasKey,
                )
                .into());
                return Ok(());
            }
            (access_key.nonce, DelegateNonceUpdate::AccessKey)
        }
        TransactionNonce::GasKeyNonce { nonce_index, .. } => {
            let Some(gas_key_info) = access_key.gas_key_info() else {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DelegateActionRequiresGasKey,
                )
                .into());
                return Ok(());
            };
            if nonce_index >= gas_key_info.num_nonces {
                result.result = Err(ActionErrorKind::DelegateActionInvalidNonceIndex {
                    nonce_index,
                    num_nonces: gas_key_info.num_nonces,
                }
                .into());
                return Ok(());
            }
            // The index is range-checked above and gas keys initialize every
            // nonce row at creation, so a missing row is inconsistent state.
            let current_nonce =
                get_gas_key_nonce(state_update, sender_id, public_key, nonce_index)?.ok_or_else(
                    || {
                        StorageError::StorageInconsistentState(format!(
                            "gas key nonce row missing for {} {} at in-range index {nonce_index} (num_nonces {})",
                            sender_id, public_key, gas_key_info.num_nonces,
                        ))
                    },
                )?;
            (current_nonce, DelegateNonceUpdate::GasKey { nonce_index })
        }
```

**File:** runtime/runtime/src/actions.rs (L604-611)
```rust
    if delegate_nonce.nonce() <= current_nonce {
        result.result = Err(ActionErrorKind::DelegateActionInvalidNonce {
            delegate_nonce: delegate_nonce.nonce(),
            ak_nonce: current_nonce,
        }
        .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L1443-1459)
```rust
        // Must fail, Nonce had been updated by previous step.
        result = ActionResult::default();
        validate_delegate_action_key(
            &mut state_update,
            &apply_state,
            (&signed_delegate_action.delegate_action).into(),
            &mut result,
        )
        .expect("Expect ok");
        assert_eq!(
            result.result,
            Err(ActionErrorKind::DelegateActionInvalidNonce {
                delegate_nonce: signed_delegate_action.delegate_action.nonce,
                ak_nonce: signed_delegate_action.delegate_action.nonce,
            }
            .into())
        );
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
