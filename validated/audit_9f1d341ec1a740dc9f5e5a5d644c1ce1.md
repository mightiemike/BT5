### Title
`DelegateAction` signed payload omits chain identity, enabling cross-chain replay by a malicious relayer — (File: `core/primitives/src/action/delegate.rs`)

### Summary

The Borsh-serialized payload that a user signs for a `DelegateAction` (NEP-366) or `DelegateActionV2` (NEP-611) contains no chain-binding identifier — no `chain_id`, `genesis_hash`, or `block_hash`. A malicious relayer can take a `DelegateAction` signed for one NEAR network (e.g., testnet) and submit it inside a valid outer `SignedTransaction` on a different network (e.g., mainnet), causing the user's mainnet account to execute actions the user never authorized for mainnet.

### Finding Description

`DelegateAction::get_nep461_hash()` computes the signed digest as:

```
hash( borsh(MessageDiscriminant(NEP_366)) || borsh(DelegateAction) )
``` [1](#0-0) 

`DelegateAction` contains `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key`: [2](#0-1) 

`DelegateActionV2` and its versioned wrapper `VersionedDelegateActionPayload` have the same structure and the same omission: [3](#0-2) [4](#0-3) 

The `MessageDiscriminant` in `signable_message.rs` is only a NEP number (a `u32`); it encodes message type, not chain identity: [5](#0-4) [6](#0-5) 

By contrast, a regular `SignedTransaction` (the outer wrapper a relayer uses) includes a `block_hash` that pins it to a specific chain and recency window: [7](#0-6) 

The outer transaction's `block_hash` protects the relayer's own signature, but it does **not** protect the inner `DelegateAction` signature. The inner signature is verified independently against the `DelegateAction` hash, which is chain-agnostic.

Mainnet and testnet both have block heights in the hundreds of millions, so a `max_block_height` of, say, `200_000_000` is simultaneously valid on both networks, providing no chain-binding protection.

### Impact Explanation

A malicious relayer can:

1. Receive a `DelegateAction` signed by Alice for testnet (e.g., `Transfer(1 NEAR)` to Bob, `nonce=5`, `max_block_height=200_000_000`).
2. Construct a valid mainnet `SignedTransaction` wrapping that exact `SignedDelegateAction` (the relayer uses their own mainnet key for the outer transaction).
3. Submit it to mainnet.
4. If Alice has the same key on mainnet and nonce 5 is valid there, the runtime accepts the inner signature and executes the transfer on mainnet.

Possible consequences: unauthorized token transfers, access key additions (account takeover), access key deletions (account lockout), or contract deployments — all on a chain the user never intended to authorize.

### Likelihood Explanation

- **Same key on multiple chains**: The vast majority of NEAR users use the same ed25519 key pair on mainnet and testnet. This is the default behavior of `near-cli` and wallets.
- **Valid nonce on target chain**: Nonces are per-key. If Alice has used nonce 1–4 on mainnet but nonce 5 on testnet, nonce 5 is valid on mainnet.
- **`max_block_height` overlap**: Both networks are in the same block-height range, so a testnet-targeted height is routinely valid on mainnet.
- **Malicious relayer**: Relayers are explicitly untrusted in the meta-transaction model (NEP-366). The protocol is designed to protect users even from malicious relayers; this is a protocol-level gap, not downstream misuse.

### Recommendation

Include a chain-binding identifier in the signed payload. The cleanest options:

1. **Add `chain_id`** (e.g., `"mainnet"`, `"testnet"`) as a field in `DelegateAction` and `DelegateActionV2`, so the Borsh-serialized payload is chain-specific.
2. **Add `genesis_hash`** to the signed data — the genesis block hash is unique per network and already available at signing time.
3. **Encode chain identity in the `MessageDiscriminant`** — extend the discriminant scheme to include a network tag alongside the NEP number.

Option 1 or 2 is preferred because it makes the chain binding explicit and verifiable by the runtime without any additional lookup.

### Proof of Concept

```
# Alice signs on testnet:
delegate_action = DelegateAction {
    sender_id:       "alice.near",
    receiver_id:     "bob.near",
    actions:         [Transfer { deposit: 1_000_000_000_000_000_000_000_000 }],
    nonce:           5,
    max_block_height: 200_000_000,   # valid on both mainnet and testnet
    public_key:      alice_ed25519_key,
}
hash = sha256( borsh(MessageDiscriminant(NEP_366)) || borsh(delegate_action) )
# hash is IDENTICAL on mainnet — no chain identity in the input

# Malicious relayer on mainnet:
outer_tx = SignedTransaction {
    signer_id:   "relayer.near",
    receiver_id: "alice.near",
    block_hash:  <valid mainnet block hash>,   # relayer's own chain binding
    actions:     [Delegate(SignedDelegateAction {
                     delegate_action: delegate_action,  # testnet-signed
                     signature:       alice_testnet_sig, # valid on mainnet too
                 })],
    nonce:       relayer_nonce,
    ...
}
# Runtime verifies alice_testnet_sig against hash — passes, because hash is chain-agnostic
# Transfer executes on mainnet without Alice's mainnet consent
```

The root cause is at: [1](#0-0) [4](#0-3)

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

**File:** core/primitives/src/signable_message.rs (L51-54)
```rust
pub struct MessageDiscriminant {
    /// The unique prefix, serialized in little-endian by borsh.
    discriminant: u32,
}
```

**File:** core/primitives/src/transaction.rs (L118-137)
```rust
#[derive(BorshSerialize, BorshDeserialize, PartialEq, Eq, Debug, Clone, ProtocolSchema)]
pub struct TransactionV1 {
    /// An account on which behalf transaction is signed
    pub signer_id: AccountId,
    /// A public key of the access key which was used to sign an account.
    /// Access key holds permissions for calling certain kinds of actions.
    pub public_key: PublicKey,
    /// Nonce is used to determine order of transaction in the pool.
    /// It increments for a combination of `signer_id` and `public_key`,
    /// and for gas key it also includes a `nonce_index`.
    pub nonce: TransactionNonce,
    /// Receiver account for this transaction
    pub receiver_id: AccountId,
    /// The hash of the block in the blockchain on top of which the given transaction is valid
    pub block_hash: CryptoHash,
    /// A list of actions to be applied
    pub actions: Vec<Action>,
    /// Controls nonce validation mode (monotonic or strict sequential).
    pub nonce_mode: NonceMode,
}
```
