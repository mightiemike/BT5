### Title
Missing Chain ID, Height, and Round in Precommit Vote Signature Domain — (`crates/apollo_signature_manager/src/signature_manager.rs`)

### Summary
`build_precommit_vote_message_digest` constructs the consensus precommit vote signature preimage as `PRECOMMIT_VOTE || block_hash` only. It omits chain ID, block height, and round number. A valid precommit signature produced on one Starknet network (or at one height/round) is cryptographically indistinguishable from a valid signature on any other network (or height/round) where the same block hash appears, enabling cross-chain and cross-context replay of validator consensus votes.

### Finding Description

`build_precommit_vote_message_digest` in `crates/apollo_signature_manager/src/signature_manager.rs` constructs the signed payload as:

```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);   // b"PRECOMMIT_VOTE"
    message.extend_from_slice(&block_hash);
    MessageDigest(blake2s_to_felt(&message))
}
``` [1](#0-0) 

The corresponding verification function accepts only `block_hash` as context:

```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    verify_signature(message_digest, signature, public_key)
}
``` [2](#0-1) 

Three domain fields are absent from the signed preimage:

1. **Chain ID** — The same validator key pair is typically used across Starknet mainnet and testnet. A precommit signature for block hash `H` on mainnet is byte-for-byte valid for block hash `H` on testnet (or any other Starknet deployment).
2. **Block height** — The `Vote` struct carries a `height: BlockNumber` field, but height is not committed into the signature. A precommit for `H` at height `N` is valid at any height where `H` reappears (e.g., two consecutive empty blocks with identical content).
3. **Round number** — The `Vote` struct carries a `round: Round` field, but round is not committed. A precommit for `H` in round `R` is valid in any other round. [3](#0-2) 

The `SignatureManager` component exposes `sign_precommit_vote` through the production RPC/component layer: [4](#0-3) 

The developers themselves flagged the incomplete domain construction with a TODO:

```
// TODO(noam.s): Consider wrapping each field in fixed delimiters (e.g. parentheses or tags) to
// avoid delimiter ambiguity across implementations; see apollo_propeller/signature.rs and PR review.
``` [5](#0-4) 

For comparison, the Propeller protocol's `build_signed_payload` correctly binds `committee_id` and `nonce` alongside the message root:

```rust
fn build_signed_payload(message_id: &MessageRoot, committee_id: CommitteeId, nonce: u64) -> Vec<u8> {
    [SIGNING_PREFIX, &message_id.0, &committee_id.0, &nonce.to_be_bytes(), SIGNING_POSTFIX].concat()
}
``` [6](#0-5) 

The `sign_precommit_vote` / `verify_precommit_vote_signature` pair is the only consensus-layer signing path that omits these binding fields.

### Impact Explanation

The precommit vote is the final cryptographic commitment a validator makes before a block is finalized by the BFT consensus engine. If a validator's precommit signature for block hash `H` is valid on any chain, height, or round where `H` appears:

- **Cross-chain replay**: An attacker who observes a quorum of mainnet precommit signatures for block hash `H` can replay them verbatim on testnet (or any other Starknet network sharing the same validator key set). If the attacker can arrange for a block with hash `H` to be proposed on the target network, `verify_precommit_vote_signature` will accept all replayed signatures as valid, forging a 2/3 quorum and causing the target network to finalize a block it should not.
- **Cross-height/round replay**: Within the same chain, if the same block hash appears at a different height or round (e.g., two empty blocks), a precommit from the first context is accepted as valid in the second, allowing an attacker to inject stale or out-of-order finalization signals into the consensus state machine.

This maps to: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload**, and potentially **Critical — Invalid or unauthorized Starknet transaction accepted through account validation, signature, nonce, chain id** (if the forged consensus leads to committing an incorrect block state).

### Likelihood Explanation

- Validator key reuse across mainnet and testnet is standard operational practice.
- Block hashes are deterministic; an attacker who controls block content on the target network can engineer a hash collision with an observed mainnet block.
- Precommit signatures are broadcast over the P2P network and are therefore observable by any peer.
- The attack requires no privileged access — any network participant can observe and replay signatures.

### Recommendation

Bind chain ID, block height, and round into the preimage before hashing:

```rust
fn build_precommit_vote_message_digest(
    chain_id: &ChainId,
    height: BlockNumber,
    round: Round,
    block_hash: BlockHash,
) -> MessageDigest {
    let mut message = Vec::new();
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(chain_id.as_hex().as_bytes()); // or canonical felt encoding
    message.extend_from_slice(&height.0.to_be_bytes());
    message.extend_from_slice(&round.to_be_bytes());
    message.extend_from_slice(&block_hash.to_bytes_be());
    MessageDigest(blake2s_to_felt(&message))
}
```

Update `sign_precommit_vote` and `verify_precommit_vote_signature` to accept and pass these fields. The `SignatureManagerRequest::SignPrecommitVote` variant and its client/server wiring must be updated accordingly.

### Proof of Concept

1. Validator `V` signs a precommit for block hash `H` on Starknet mainnet at height `N`, round `R`.
   - Signed digest: `blake2s(b"PRECOMMIT_VOTE" || H)`
   - Signature: `σ = ECDSA_sign(private_key_V, digest)`

2. Attacker observes `σ` from the P2P broadcast.

3. On Starknet testnet, attacker proposes (or waits for) a block whose hash is also `H` (achievable by controlling block content, e.g., identical calldata/timestamp).

4. Attacker injects a `Vote { vote_type: Precommit, height: N', round: R', proposal_commitment: H, voter: V, signature: σ }` into the testnet consensus.

5. Testnet calls `verify_precommit_vote_signature(H, σ, V.public_key)`:
   - Computes `blake2s(b"PRECOMMIT_VOTE" || H)` — identical to step 1.
   - ECDSA verification passes: returns `true`.

6. Testnet consensus counts this as a valid precommit from `V`. Repeating for enough validators forges a 2/3 quorum, causing testnet to finalize block `H` without genuine validator agreement. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L31-33)
```rust
// Message domain separators.
pub(crate) const INIT_PEER_ID: &[u8] = b"INIT_PEER_ID";
pub(crate) const PRECOMMIT_VOTE: &[u8] = b"PRECOMMIT_VOTE";
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L68-74)
```rust
    pub async fn sign_precommit_vote(
        &self,
        block_hash: BlockHash,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_precommit_vote_message_digest(block_hash);
        self.sign(message_digest).await
    }
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L122-124)
```rust
// TODO(noam.s): Consider wrapping each field in fixed delimiters (e.g. parentheses or tags) to
// avoid delimiter ambiguity across implementations; see apollo_propeller/signature.rs and PR
// review.
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L138-145)
```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash);

    MessageDigest(blake2s_to_felt(&message))
}
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L179-186)
```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    verify_signature(message_digest, signature, public_key)
}
```

**File:** crates/apollo_protobuf/src/consensus.rs (L53-61)
```rust
#[derive(Debug, Default, Hash, Clone, Eq, PartialEq, Serialize, Deserialize)]
pub struct Vote {
    pub vote_type: VoteType,
    pub height: BlockNumber,
    pub round: Round,
    pub proposal_commitment: Option<ProposalCommitment>,
    pub voter: ContractAddress,
    pub signature: RawSignature,
}
```

**File:** crates/apollo_signature_manager/src/communication.rs (L30-34)
```rust
            SignatureManagerRequest::SignPrecommitVote(block_hash) => {
                SignatureManagerResponse::SignPrecommitVote(
                    self.sign_precommit_vote(block_hash).await,
                )
            }
```

**File:** crates/apollo_propeller/src/signature.rs (L88-94)
```rust
fn build_signed_payload(
    message_id: &MessageRoot,
    committee_id: CommitteeId,
    nonce: u64,
) -> Vec<u8> {
    [SIGNING_PREFIX, &message_id.0, &committee_id.0, &nonce.to_be_bytes(), SIGNING_POSTFIX].concat()
}
```
