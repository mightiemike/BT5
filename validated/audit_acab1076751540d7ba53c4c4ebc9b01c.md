### Title
`DelegateAction` Signed Payload Omits `chain_id`, Enabling Cross-Network Replay by a Malicious Relayer — (`File: core/primitives/src/action/delegate.rs`)

### Summary

`DelegateAction` (NEP-366 meta-transactions) and `DelegateActionV2` (NEP-611 gas-key meta-transactions) produce their signing hash over a payload that contains no network identifier (`chain_id`). A malicious relayer can take a `SignedDelegateAction` that a user signed for one NEAR network (e.g., testnet) and replay it on a different NEAR network (e.g., mainnet) by wrapping it in a fresh outer `SignedTransaction` whose `block_hash` is from the target chain.

### Finding Description

`DelegateAction::get_nep461_hash()` constructs the signed payload as:

```
SHA-256( Borsh( MessageDiscriminant(NEP-366) || DelegateAction ) )
```

where `DelegateAction` contains `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key`. [1](#0-0) 

No `chain_id` (the string `"mainnet"`, `"testnet"`, etc.) is included anywhere in this serialized payload. [2](#0-1) 

The `MessageDiscriminant` only distinguishes message *type* (NEP-366 vs NEP-611), not the *network* on which the message is intended to execute. [3](#0-2) 

The outer `SignedTransaction` that wraps the `DelegateAction` does include a `block_hash`, which binds the *outer* transaction to a specific chain. However, the inner `DelegateAction` signature is verified independently: [4](#0-3) 

`SignedDelegateAction::verify()` calls `get_nep461_hash()` and checks the signature against the user's public key — with no chain-identity check. A malicious relayer can therefore:

1. Receive a `SignedDelegateAction` from Alice intended for testnet.
2. Construct a new outer `SignedTransaction` on mainnet, embedding Alice's unchanged `SignedDelegateAction`.
3. Sign the outer transaction with a mainnet `block_hash` (the relayer's own key pays gas).
4. Submit to mainnet. The inner `DelegateAction` signature passes `verify()` because the hash is chain-agnostic.

`DelegateActionV2` / `VersionedDelegateActionPayload` has the identical omission: [5](#0-4) [6](#0-5) 

### Impact Explanation

If a user holds the same account name and key pair on both mainnet and testnet (common for developers and power users), a malicious relayer can replay any `SignedDelegateAction` the user signed for testnet onto mainnet. The inner actions execute with `sender_id` = the user's mainnet account, draining mainnet tokens or executing arbitrary mainnet contract calls the user never authorized on mainnet. The nonce guard only prevents replay *within* the same network; it does not prevent cross-network replay because nonce sequences are independent per network.

### Likelihood Explanation

- NEAR account names are shared across mainnet and testnet; many users and developers hold the same key on both.
- The relayer role is explicitly unprivileged in the protocol — any party can act as a relayer.
- The user has no protocol-level mechanism to restrict their `SignedDelegateAction` to a specific network; they must trust the relayer entirely.
- `max_block_height` provides only a time-window bound, not a chain-identity bound.

### Recommendation

Include the genesis `chain_id` string in the Borsh-serialized payload that is hashed for the `DelegateAction` signature. Concretely, add a `chain_id: String` field to `DelegateAction` and `DelegateActionV2`, or include it in the `SignableMessage` wrapper before hashing. This mirrors the fix described in the external report (reading the chain identifier from the chain rather than relying on an external party to enforce it). The `chain_id` is available at signing time from genesis config and at verification time from `ApplyState`.

### Proof of Concept

```
// Alice on testnet signs:
let delegate_action = DelegateAction {
    sender_id: "alice.near",   // same account exists on mainnet
    receiver_id: "bob.near",
    actions: vec![Transfer { deposit: 100_000_000_000_000_000_000_000 }],
    nonce: 1,                  // valid on mainnet (independent nonce sequence)
    max_block_height: 999_999_999,
    public_key: alice_key,     // same key registered on mainnet
};
// Signed with NEP-461 hash — no chain_id in payload
let sig = alice_key.sign(delegate_action.get_nep461_hash().as_bytes());
let signed_da = SignedDelegateAction { delegate_action, signature: sig };

// Malicious relayer on mainnet:
let outer_tx = SignedTransaction::from_actions(
    relayer_nonce,
    relayer_mainnet_account,
    "alice.near",              // outer receiver = alice's mainnet account
    &relayer_mainnet_key,
    vec![Action::Delegate(Box::new(signed_da))],
    mainnet_block_hash,        // valid mainnet block hash
);
// submit outer_tx to mainnet RPC
// → signed_da.verify() passes (hash is chain-agnostic)
// → alice's mainnet tokens transferred to bob.near
```

The divergent Borsh bytes are the four bytes of `MessageDiscriminant` (`0x6E 0x04 0x00 0x40` for NEP-366) followed by the `DelegateAction` body — identical on every NEAR network, with no byte that encodes which network the signature is bound to.

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

**File:** core/primitives/src/action/delegate.rs (L83-96)
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
}
```

**File:** core/primitives/src/action/delegate.rs (L119-133)
```rust
pub struct DelegateActionV2 {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce of the signing key, advanced when this action is processed. For
    /// a gas key it also selects which of the parallel nonces to advance.
    pub nonce: TransactionNonce,
    /// The maximal height of the block in the blockchain below which the given DelegateActionV2 is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

**File:** core/primitives/src/action/delegate.rs (L176-185)
```rust
    /// Delegate action hash used for NEP-461 signature scheme which tags
    /// different messages before hashing
    ///
    /// For more details, see: [NEP-461](https://github.com/near/NEPs/pull/461)
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateActionV2);
        let bytes = borsh::to_vec(&signable).expect("failed to serialize");
        hash(&bytes)
    }
}
```

**File:** core/primitives/src/action/delegate.rs (L349-358)
```rust
    /// Delegate action hash used for NEP-461 signature scheme which tags
    /// different messages before hashing
    ///
    /// For more details, see: [NEP-461](https://github.com/near/NEPs/pull/461)
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
        let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
        hash(&bytes)
    }
}
```

**File:** core/primitives/src/signable_message.rs (L18-25)
```rust
const MIN_ON_CHAIN_DISCRIMINANT: u32 = 1 << 30;
const MAX_ON_CHAIN_DISCRIMINANT: u32 = (1 << 31) - 1;
const MIN_OFF_CHAIN_DISCRIMINANT: u32 = 1 << 31;
const MAX_OFF_CHAIN_DISCRIMINANT: u32 = u32::MAX;

// NEPs currently included in the scheme
const NEP_366_META_TRANSACTIONS: u32 = 366;
const NEP_611_GAS_KEYS: u32 = 611;
```
