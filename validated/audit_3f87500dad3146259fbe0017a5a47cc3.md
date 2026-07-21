### Title
Missing Chain ID in Precommit Vote Signature Domain Enables Cross-Network Replay — (`File: crates/apollo_signature_manager/src/signature_manager.rs`)

### Summary

`build_precommit_vote_message_digest` constructs the ECDSA signing preimage as `PRECOMMIT_VOTE || block_hash_bytes` with no chain identifier. A precommit vote signature produced by a sequencer/validator on one Starknet network (e.g., `SN_MAIN`) is cryptographically valid on any other Starknet network (e.g., `SN_SEPOLIA`) where the same block hash value appears, because the domain is not bound to a specific chain.

### Finding Description

`build_precommit_vote_message_digest` in `crates/apollo_signature_manager/src/signature_manager.rs` builds the message to be signed as:

```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash);
    MessageDigest(blake2s_to_felt(&message))
}
``` [1](#0-0) 

The domain separator is the static byte string `b"PRECOMMIT_VOTE"` with no chain ID, network identifier, or any other chain-binding field. [2](#0-1) 

`sign_precommit_vote` and `verify_precommit_vote_signature` are both production-exposed APIs, wired through the `SignatureManagerRequest::SignPrecommitVote` component request handler: [3](#0-2) 

The `SignatureManagerRequest::SignPrecommitVote(BlockHash)` type carries only the block hash — no chain ID is threaded through the call path: [4](#0-3) 

For comparison, every Starknet transaction hash function correctly binds `chain_id` into the preimage (e.g., `get_invoke_transaction_v3_hash` chains `Felt::try_from(chain_id)?`): [5](#0-4) 

The block hash itself also does **not** include chain ID — `calculate_block_hash` hashes `BLOCK_HASH_VERSION, block_number, state_root, sequencer_address, timestamp, ...` with no chain binding: [6](#0-5) 

This means the entire chain of `block_hash → precommit_digest → signature` is chain-agnostic.

### Impact Explanation

A precommit vote signature `σ = ECDSA_sign(sk, blake2s("PRECOMMIT_VOTE" || H))` produced by validator `V` on network A for block hash `H` is byte-for-byte identical to a valid precommit signature for block hash `H` on any other network. If an adversary can arrange for the same block hash `H` to appear on network B (or observe it from a fork/testnet), they can replay `V`'s signature to contribute to a false BFT quorum on network B without `V`'s participation or consent. This maps to: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

Block hash collisions across networks are not random — they require matching `block_number`, `sequencer_address`, `timestamp`, `state_root`, and all commitment fields simultaneously, which is practically infeasible under normal operation. However, the structural absence of chain binding is a latent invariant violation: any future scenario where block hashes are predictable or shared (e.g., a network fork, a testnet that mirrors mainnet state, or a deliberate grinding attack) would make the replay directly exploitable. Additionally, the state machine currently sets `signature: RawSignature::default()` with a `TODO(Asmaa): sign the vote` comment, meaning full enforcement is not yet active — but the signing infrastructure is already deployed and the flaw will be live when that TODO is resolved. [7](#0-6) 

### Recommendation

Include `chain_id` in the precommit vote signing preimage. The simplest fix is to add the chain ID bytes between the domain separator and the block hash in `build_precommit_vote_message_digest`:

```rust
fn build_precommit_vote_message_digest(
    block_hash: BlockHash,
    chain_id: &ChainId,
) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let chain_id_bytes = chain_id.as_hex_str().as_bytes(); // or canonical felt encoding
    let mut message = Vec::new();
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(chain_id_bytes);
    message.extend_from_slice(&block_hash);
    MessageDigest(blake2s_to_felt(&message))
}
```

`SignatureManager` should be constructed with a `chain_id` field (analogous to how `TransactionConverter` holds `self.chain_id`), and `SignatureManagerRequest::SignPrecommitVote` should carry the chain ID or the manager should enforce it internally. `verify_precommit_vote_signature` must be updated symmetrically.

### Proof of Concept

1. Validator `V` operates on both `SN_MAIN` (network A) and `SN_SEPOLIA` (network B) with the same signing key (a realistic scenario for a multi-network operator).
2. At height `H`, network A produces block hash `BH_A`. `V` calls `sign_precommit_vote(BH_A)` → signature `σ`.
3. An adversary observes `σ` from the network A gossip channel.
4. The adversary constructs a scenario on network B where block hash `BH_B == BH_A` (e.g., by controlling the proposer on a fork, or by waiting for a testnet that mirrors mainnet block structure).
5. The adversary injects `σ` as `V`'s precommit vote on network B. `verify_precommit_vote_signature(BH_B, σ, V.public_key)` returns `true` because the digest `blake2s("PRECOMMIT_VOTE" || BH_A)` equals `blake2s("PRECOMMIT_VOTE" || BH_B)`.
6. `V`'s vote weight is counted toward quorum on network B for a block `V` never actually validated, potentially finalizing a wrong or adversary-controlled block. [8](#0-7) [9](#0-8)

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

**File:** crates/apollo_signature_manager/src/communication.rs (L30-34)
```rust
            SignatureManagerRequest::SignPrecommitVote(block_hash) => {
                SignatureManagerResponse::SignPrecommitVote(
                    self.sign_precommit_vote(block_hash).await,
                )
            }
```

**File:** crates/apollo_signature_manager_types/src/lib.rs (L92-95)
```rust
pub enum SignatureManagerRequest {
    SignIdentification(PeerId, Challenge),
    SignPrecommitVote(BlockHash),
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L388-398)
```rust
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
}
```

**File:** crates/apollo_consensus/src/state_machine.rs (L248-256)
```rust
        let vote = Vote {
            vote_type,
            height: self.height,
            round: self.round,
            proposal_commitment,
            voter: self.id,
            // TODO(Asmaa): sign the vote
            signature: RawSignature::default(),
        };
```
