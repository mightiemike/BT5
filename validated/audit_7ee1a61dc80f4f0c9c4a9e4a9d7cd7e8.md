Now I have enough context to analyze the analog. Let me look at one more detail to confirm the finding.

### Title
`DelegateAction` Signed Payload Lacks Chain-Specific Domain Separator, Enabling Cross-Network Replay — (`File: core/primitives/src/action/delegate.rs`)

### Summary

`DelegateAction` (meta-transaction / NEP-366) and `DelegateActionV2` (NEP-611) produce a signed hash that contains no chain-specific component. The `SignableMessage` discriminant is a fixed integer (`2^30 + 366` or `2^30 + 611`) that is identical on every NEAR network. A malicious relayer who receives a signed `DelegateAction` on one network (e.g., testnet) can replay it verbatim on another network (e.g., mainnet) if the account exists on both with a compatible nonce and a still-valid `max_block_height`.

### Finding Description

`DelegateAction.get_nep461_hash()` constructs the signed payload as:

```
SHA256( LE32(MIN_ON_CHAIN_DISCRIMINANT + NEP_366) || borsh(DelegateAction) )
```

where `MIN_ON_CHAIN_DISCRIMINANT = 2^30` and `NEP_366 = 366`, giving a fixed 4-byte prefix `0xCE760040` on every NEAR network. [1](#0-0) 

The `DelegateAction` struct itself contains:
- `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, `public_key`

None of these fields are chain-specific. [2](#0-1) 

The hash is computed and verified without any `block_hash` or `chain_id`: [3](#0-2) [4](#0-3) 

By contrast, regular `TransactionV0` includes `block_hash` — a network-specific value that makes cross-network replay of ordinary transactions impossible: [5](#0-4) 

`DelegateActionV2` / `VersionedDelegateActionPayload` has the identical structural gap — its `get_nep461_hash()` uses discriminant `2^30 + 611`, also a constant across all networks: [6](#0-5) 

The `SignableMessage` wrapper adds only a NEP-number discriminant, not a chain identifier: [7](#0-6) 

### Impact Explanation

A user who signs a `DelegateAction` on testnet (or any NEAR network) to authorize a relayer to execute actions on their behalf produces a signature that is cryptographically valid on every other NEAR network where:
1. The same `sender_id` account exists.
2. The access key nonce is compatible (monotonic mode: any nonce strictly greater than the current key nonce; strict mode: exactly `ak_nonce + 1`).
3. The `max_block_height` has not yet been reached on the target network.

A malicious relayer can replay the signed action on mainnet to execute arbitrary `NonDelegateAction` operations — including token transfers, `AddKey`/`DeleteKey`, `FunctionCall`, and `DeployContract` — on behalf of the victim without their consent on that network.

### Likelihood Explanation

- Developers routinely hold the same account name on both testnet and mainnet.
- Fresh accounts or accounts with few transactions have low nonces that are trivially compatible.
- Block heights on mainnet and testnet are in the same order of magnitude; a `max_block_height` set generously (e.g., current height + 100) is valid on both networks simultaneously.
- The relayer role is explicitly unprivileged and untrusted in the meta-transaction model; the protocol is supposed to protect users from a malicious relayer via the signed payload, but the missing chain domain separator breaks this guarantee.

### Recommendation

Include a chain-specific domain component in the `DelegateAction` signed payload. The two standard approaches are:

1. **Add `block_hash`** to `DelegateAction` (mirroring `TransactionV0`). This binds the signature to a specific block on a specific network and also provides a validity window, replacing `max_block_height`.

2. **Include `chain_id`** in the `SignableMessage` discriminant or as a prefix field. The genesis `chain_id` string (`"mainnet"`, `"testnet"`, etc.) is already available in the runtime and is returned by the `chain_id` host function. [8](#0-7) 

Either approach makes the signed hash network-specific, preventing cross-network replay without breaking the relayer model.

### Proof of Concept

1. Alice holds account `alice.near` on both testnet and mainnet, each with access key nonce `5`.
2. Alice signs a `DelegateAction` on testnet:
   - `sender_id = "alice.near"`, `receiver_id = "bob.near"`, `actions = [Transfer { deposit: 10 NEAR }]`, `nonce = 6`, `max_block_height = 200_000_000`, `public_key = alice_key`
   - Signed hash = `SHA256(LE32(1073742222) || borsh(DelegateAction))` — identical on mainnet.
3. Alice sends the `SignedDelegateAction` to a relayer on testnet.
4. The malicious relayer ignores testnet and instead submits the `SignedDelegateAction` inside a mainnet transaction addressed to `alice.near`.
5. The mainnet runtime calls `SignedDelegateAction::verify()`:
   - Recomputes `delegate_action.get_nep461_hash()` → same 32-byte hash as on testnet.
   - `self.signature.verify(hash.as_ref(), public_key)` → **passes**, because the key and signature are valid. [9](#0-8) 
6. The transfer of 10 NEAR executes on mainnet without Alice's consent for that network.

### Citations

**File:** core/primitives/src/signable_message.rs (L18-24)
```rust
const MIN_ON_CHAIN_DISCRIMINANT: u32 = 1 << 30;
const MAX_ON_CHAIN_DISCRIMINANT: u32 = (1 << 31) - 1;
const MIN_OFF_CHAIN_DISCRIMINANT: u32 = 1 << 31;
const MAX_OFF_CHAIN_DISCRIMINANT: u32 = u32::MAX;

// NEPs currently included in the scheme
const NEP_366_META_TRANSACTIONS: u32 = 366;
```

**File:** core/primitives/src/signable_message.rs (L61-107)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[non_exhaustive]
pub enum SignableMessageType {
    /// A delegate action, intended for a relayer to included it in an action list of a transaction.
    DelegateAction,
    /// A delegate action with gas key support, intended for a relayer to include it in an action
    /// list of a transaction.
    DelegateActionV2,
}

#[derive(thiserror::Error, Debug)]
#[non_exhaustive]
pub enum ReadDiscriminantError {
    #[error("does not fit any known categories")]
    UnknownMessageType,
    #[error("NEP {0} does not have a known on-chain use")]
    UnknownOnChainNep(u32),
    #[error("NEP {0} does not have a known off-chain use")]
    UnknownOffChainNep(u32),
    #[error("discriminant is in the range for transactions")]
    TransactionFound,
}

#[derive(thiserror::Error, Debug)]
#[non_exhaustive]
pub enum CreateDiscriminantError {
    #[error("nep number {0} is too big")]
    NepTooLarge(u32),
}

impl<'a, T: BorshSerialize> SignableMessage<'a, T> {
    pub fn new(msg: &'a T, ty: SignableMessageType) -> Self {
        let discriminant = ty.into();
        Self { discriminant, msg }
    }

    pub fn sign(&self, signer: &Signer) -> Signature {
        let bytes = borsh::to_vec(&self).expect("Failed to deserialize");
        let hash = hash(&bytes);
        signer.sign(hash.as_bytes())
    }
```

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

**File:** core/primitives/src/action/delegate.rs (L83-95)
```rust
impl SignedDelegateAction {
    pub fn verify(&self) -> bool {
        let delegate_action = &self.delegate_action;
        let hash = delegate_action.get_nep461_hash();
        let public_key = &delegate_action.public_key;

        self.signature.verify(hash.as_ref(), public_key)
    }

    pub fn sign(singer: &Signer, delegate_action: DelegateAction) -> Self {
        let signature = singer.sign(delegate_action.get_nep461_hash().as_bytes());
        Self { delegate_action, signature }
    }
```

**File:** core/primitives/src/action/delegate.rs (L180-184)
```rust
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateActionV2);
        let bytes = borsh::to_vec(&signable).expect("failed to serialize");
        hash(&bytes)
    }
```

**File:** core/primitives/src/action/delegate.rs (L353-357)
```rust
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
        let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
        hash(&bytes)
    }
```

**File:** core/primitives/src/transaction.rs (L33-48)
```rust
pub struct TransactionV0 {
    /// An account on which behalf transaction is signed
    pub signer_id: AccountId,
    /// A public key of the access key which was used to sign an account.
    /// Access key holds permissions for calling certain kinds of actions.
    pub public_key: PublicKey,
    /// Nonce is used to determine order of transaction in the pool.
    /// It increments for a combination of `signer_id` and `public_key`
    pub nonce: Nonce,
    /// Receiver account for this transaction
    pub receiver_id: AccountId,
    /// The hash of the block in the blockchain on top of which the given transaction is valid
    pub block_hash: CryptoHash,
    /// A list of actions to be applied
    pub actions: Vec<Action>,
}
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L666-675)
```rust
    pub fn chain_id(&mut self, register_id: u64) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        let chain_id = self.ext.chain_id();
        self.registers.set(
            &mut self.result_state.gas_counter,
            &self.config.limit_config,
            register_id,
            chain_id.as_bytes(),
        )
    }
```
