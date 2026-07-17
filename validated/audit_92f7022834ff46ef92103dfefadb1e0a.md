### Title
`ActionView::DelegateV2` Borsh Discriminant `16` Diverges from `Action::DelegateV2` Discriminant `14`, Breaking RPC Borsh Type Compatibility — (`File: core/primitives/src/views.rs`)

---

### Summary

`ActionView` derives `BorshSerialize`/`BorshDeserialize` and assigns `DelegateV2` the Borsh discriminant `16`, while the canonical `Action::DelegateV2` uses discriminant `14`. Simultaneously, `ActionView::TransferToGasKey` occupies discriminant `14`. Any Borsh round-trip that crosses the `Action`/`ActionView` boundary silently misidentifies `DelegateV2` as `TransferToGasKey` (or fails to parse), with no error surfaced to the caller.

---

### Finding Description

In `core/primitives/src/action/mod.rs`, the `Action` enum assigns:

```
Action::DelegateV2(...) = 14
``` [1](#0-0) 

In `core/primitives/src/views.rs`, the `ActionView` enum assigns:

```
ActionView::TransferToGasKey { ... } = 14
ActionView::WithdrawFromGasKey { ... } = 15
ActionView::DelegateV2 { ... } = 16
``` [2](#0-1) 

Both `Action` and `ActionView` derive `BorshSerialize` and `BorshDeserialize`: [3](#0-2) 

The `From<Action> for ActionView` conversion correctly maps `Action::DelegateV2` → `ActionView::DelegateV2` at the type level: [4](#0-3) 

However, the Borsh encoding of the resulting `ActionView` emits discriminant byte `16`, not `14`. The two enums share the same discriminant space (both use `#[borsh(use_discriminant = true)]` / `#[repr(u8)]`) but assign conflicting values:

| Discriminant | `Action` variant | `ActionView` variant |
|---|---|---|
| `12` | `TransferToGasKey` | — |
| `13` | `WithdrawFromGasKey` | `DeterministicStateInit` |
| `14` | **`DelegateV2`** | **`TransferToGasKey`** |
| `15` | — | `WithdrawFromGasKey` |
| `16` | — | **`DelegateV2`** |

Any code path that Borsh-deserializes `Action` bytes as `ActionView` will silently misread a `DelegateV2` action (byte `14`) as `TransferToGasKey`. Any code path that Borsh-deserializes `ActionView` bytes as `Action` will fail to parse a `DelegateV2` view (byte `16`) because no `Action` variant carries that discriminant.

---

### Impact Explanation

**Silent semantic corruption (High):** When `Action::DelegateV2` bytes (discriminant `14`) are Borsh-deserialized as `ActionView`, the decoder silently produces `ActionView::TransferToGasKey` — a structurally different variant with a different field layout. The deserialization does not return an error; it reads the wrong fields from the byte stream. Any downstream logic that inspects the resulting `ActionView` will operate on corrupted data without any indication of failure.

**Hard parse failure (Medium):** When `ActionView::DelegateV2` bytes (discriminant `16`) are Borsh-deserialized as `Action`, the decoder returns an `InvalidData` error because `Action` has no variant `16`. This surfaces as a hard failure rather than silent corruption, but it breaks any Borsh-based interchange of view data.

Both failure modes are triggered by any `DelegateV2` action submitted by an unprivileged user after protocol version 85 activates `ProtocolFeature::DelegateV2`. [5](#0-4) 

---

### Likelihood Explanation

`ProtocolFeature::DelegateV2` is stabilized at protocol version 85 alongside `GasKeys`, `StrictNonce`, `PostQuantumSignatures`, and others. Any user who submits a `DelegateV2` action (a meta-transaction with gas-key support) after the feature activates will produce an `Action::DelegateV2` that, when converted to `ActionView` and Borsh-serialized, carries discriminant `16` instead of `14`. The mismatch is unconditional — it is not gated by any runtime check or configuration flag. The `ActionView` Borsh derives are present in production code and the type is used in `SignedTransactionView`, which is the RPC-facing representation of transactions. [6](#0-5) 

---

### Recommendation

Align `ActionView::DelegateV2`'s Borsh discriminant with `Action::DelegateV2`'s discriminant (`14`). Because `ActionView::TransferToGasKey` currently occupies `14` and `ActionView::WithdrawFromGasKey` occupies `15`, the `ActionView` variants added alongside `GasKeys`/`DelegateV2` must be renumbered to match the canonical `Action` layout exactly. The correct mapping is:

| Discriminant | `Action` | `ActionView` (corrected) |
|---|---|---|
| `12` | `TransferToGasKey` | `TransferToGasKey` |
| `13` | `WithdrawFromGasKey` | `WithdrawFromGasKey` |
| `14` | `DelegateV2` | `DelegateV2` |

Any existing Borsh-serialized `ActionView` blobs stored or transmitted with the old discriminant layout would need a migration.

---

### Proof of Concept

**Exact divergent bytes:**

```
Action::DelegateV2   → Borsh byte[0] = 0x0E (14)
ActionView::DelegateV2 → Borsh byte[0] = 0x10 (16)
ActionView::TransferToGasKey → Borsh byte[0] = 0x0E (14)  ← collision
```

**Concrete mismatch:**

```rust
// Action::DelegateV2 = 14 (wire format)
let action = Action::DelegateV2(Box::new(VersionedSignedDelegateAction { ... }));
let action_bytes = borsh::to_vec(&action).unwrap();
assert_eq!(action_bytes[0], 14u8);  // passes

// ActionView::DelegateV2 = 16 (view format)
let view = ActionView::from(action);
let view_bytes = borsh::to_vec(&view).unwrap();
assert_eq!(view_bytes[0], 16u8);  // passes — but diverges from Action

// Cross-decode: Action bytes → ActionView
// Discriminant 14 → ActionView::TransferToGasKey (WRONG, silent corruption)
let wrong: ActionView = borsh::from_slice(&action_bytes).unwrap();
// wrong is ActionView::TransferToGasKey, not ActionView::DelegateV2

// Cross-decode: ActionView bytes → Action
// Discriminant 16 → no Action variant → hard error
let err = Action::try_from_slice(&view_bytes);
assert!(err.is_err());  // InvalidData
``` [7](#0-6) [8](#0-7)

### Citations

**File:** core/primitives/src/action/mod.rs (L347-370)
```rust
#[borsh(use_discriminant = true)]
#[repr(u8)]
pub enum Action {
    /// Create an (sub)account using a transaction `receiver_id` as an ID for
    /// a new account ID must pass validation rules described here
    /// <https://nomicon.io/DataStructures/Account>.
    CreateAccount(CreateAccountAction) = 0,
    /// Sets a Wasm code to a receiver_id
    DeployContract(DeployContractAction) = 1,
    FunctionCall(Box<FunctionCallAction>) = 2,
    Transfer(TransferAction) = 3,
    Stake(Box<StakeAction>) = 4,
    AddKey(Box<AddKeyAction>) = 5,
    DeleteKey(Box<DeleteKeyAction>) = 6,
    DeleteAccount(DeleteAccountAction) = 7,
    Delegate(Box<delegate::SignedDelegateAction>) = 8,
    DeployGlobalContract(DeployGlobalContractAction) = 9,
    UseGlobalContract(Box<UseGlobalContractAction>) = 10,
    DeterministicStateInit(Box<DeterministicStateInitAction>) = 11,
    TransferToGasKey(Box<TransferToGasKeyAction>) = 12,
    WithdrawFromGasKey(Box<WithdrawFromGasKeyAction>) = 13,
    /// Meta transaction carrying a `DelegateActionV2`, which supports gas keys.
    DelegateV2(Box<delegate::VersionedSignedDelegateAction>) = 14,
}
```

**File:** core/primitives/src/views.rs (L1438-1530)
```rust
#[serde_as]
#[derive(
    BorshSerialize,
    BorshDeserialize,
    Clone,
    Debug,
    PartialEq,
    Eq,
    serde::Serialize,
    serde::Deserialize,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
#[borsh(use_discriminant = true)]
#[repr(u8)]
pub enum ActionView {
    CreateAccount = 0,
    DeployContract {
        #[serde_as(as = "Base64")]
        #[cfg_attr(
            feature = "schemars",
            schemars(schema_with = "crate::serialize::base64_schema")
        )]
        code: Vec<u8>,
    } = 1,
    FunctionCall {
        method_name: String,
        args: FunctionArgs,
        gas: Gas,
        deposit: Balance,
    } = 2,
    Transfer {
        deposit: Balance,
    } = 3,
    Stake {
        stake: Balance,
        public_key: PublicKey,
    } = 4,
    AddKey {
        public_key: PublicKey,
        access_key: AccessKeyView,
    } = 5,
    DeleteKey {
        public_key: PublicKey,
    } = 6,
    DeleteAccount {
        beneficiary_id: AccountId,
    } = 7,
    Delegate {
        delegate_action: DelegateAction,
        signature: Signature,
    } = 8,
    DelegateV2 {
        delegate_action: VersionedDelegateActionPayload,
        signature: Signature,
    } = 16,
    DeployGlobalContract {
        #[serde_as(as = "Base64")]
        #[cfg_attr(
            feature = "schemars",
            schemars(schema_with = "crate::serialize::base64_schema")
        )]
        code: Vec<u8>,
    } = 9,
    DeployGlobalContractByAccountId {
        #[serde_as(as = "Base64")]
        #[cfg_attr(
            feature = "schemars",
            schemars(schema_with = "crate::serialize::base64_schema")
        )]
        code: Vec<u8>,
    } = 10,
    UseGlobalContract {
        code_hash: CryptoHash,
    } = 11,
    UseGlobalContractByAccountId {
        account_id: AccountId,
    } = 12,
    DeterministicStateInit {
        code: GlobalContractIdentifierView,
        #[serde_as(as = "BTreeMap<Base64, Base64>")]
        #[cfg_attr(feature = "schemars", schemars(with = "BTreeMap<String, String>"))]
        data: BTreeMap<Vec<u8>, Vec<u8>>,
        deposit: Balance,
    } = 13,
    TransferToGasKey {
        public_key: PublicKey,
        deposit: Balance,
    } = 14,
    WithdrawFromGasKey {
        public_key: PublicKey,
        amount: Balance,
    } = 15,
}
```

**File:** core/primitives/src/views.rs (L1562-1565)
```rust
            Action::DelegateV2(action) => ActionView::DelegateV2 {
                delegate_action: action.delegate_action,
                signature: action.signature,
            },
```

**File:** core/primitives/src/views.rs (L1698-1713)
```rust
pub struct SignedTransactionView {
    pub signer_id: AccountId,
    pub public_key: PublicKey,
    pub nonce: Nonce,
    pub receiver_id: AccountId,
    pub actions: Vec<ActionView>,
    /// Deprecated, retained for backward compatibility.
    #[serde(default, rename = "priority_fee")]
    pub _priority_fee: u64,
    pub signature: Signature,
    pub hash: CryptoHash,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub nonce_index: Option<NonceIndex>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub nonce_mode: Option<NonceMode>,
}
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
