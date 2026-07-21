### Title
Precommit Vote Signature Domain Omits `chain_id`, Enabling Cross-Chain Replay - (`crates/apollo_signature_manager/src/signature_manager.rs`)

### Summary

`build_precommit_vote_message_digest` constructs the signed preimage as `blake2s(b"PRECOMMIT_VOTE" || block_hash_bytes)`. It does not bind the signature to any chain-specific identifier. A precommit signature produced on one Starknet network is cryptographically valid on any other network whose block hash for a given block happens to be identical — a condition that holds for any two networks sharing the same genesis and block content up to that point (e.g., a testnet fork of mainnet, or a chain replay scenario).

### Finding Description

`build_precommit_vote_message_digest` in `crates/apollo_signature_manager/src/signature_manager.rs` constructs the message digest as:

```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);   // b"PRECOMMIT_VOTE"
    message.extend_from_slice(&block_hash);
    MessageDigest(blake2s_to_felt(&message))
}
```

The domain separator `PRECOMMIT_VOTE` is a fixed ASCII constant with no chain-specific component. The `chain_id` is never included. The `Vote` struct carries `height`, `round`, `vote_type`, `proposal_commitment`, `voter`, and `signature`, but only `proposal_commitment` (the block hash) is covered by the signature.

The direct analog to the ERC-7739 bug is exact: ERC-7739 used the SmartSession address (shared across all accounts) as `verifyingContract` instead of the individual account address, making all accounts produce the same hash modification. Here, the precommit vote domain uses a fixed constant `PRECOMMIT_VOTE` with no `chain_id`, making all chains produce the same signed digest for the same block hash.

The `sign_precommit_vote` and `verify_precommit_vote_signature` are production-facing APIs exposed through the `SignatureManagerClient` trait:

```rust
pub async fn sign_precommit_vote(
    &self,
    block_hash: BlockHash,
) -> SignatureManagerResult<RawSignature> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    self.sign(message_digest).await
}
```

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

The `calculate_block_hash` function does include `block_number` in the block hash preimage, so the same block hash cannot appear at two different heights on the same chain. However, `chain_id` is absent from `calculate_block_hash`:

```rust
HashChain::new()
    .chain(&block_hash_version.clone().into())
    .chain(&partial_block_hash_components.block_number.0.into())
    .chain(&state_root.0)
    .chain(&partial_block_hash_components.sequencer.0)
    // ... no chain_id ...
    .chain(&previous_block_hash.0)
    .get_poseidon_hash()
```

Two networks sharing the same genesis block and identical block content up to block N will produce identical block hashes for block N. A precommit signature from a validator on network A is then a valid precommit signature on network B for the same block hash.

### Impact Explanation

An attacker who observes a valid precommit signature `(r, s)` from validator V for block hash `H` on chain A can submit that exact signature as V's precommit for block hash `H` on chain B (a fork or testnet sharing genesis). The `verify_precommit_vote_signature` call on chain B will return `true` because the message digest is identical. This allows an attacker to forge consensus precommit votes on one chain using signatures collected from another, potentially contributing to a false quorum or disrupting the BFT safety guarantee.

This matches the impact category: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

The precondition is two live Starknet networks sharing the same genesis block and identical block content up to some block N. This is realistic for:
- A testnet that was forked from mainnet state at a snapshot
- A chain replay or re-genesis scenario
- Any two networks that happen to share genesis parameters

The signing infrastructure is production code exposed through `SignatureManagerClient`. The state machine has a `TODO(Asmaa): sign the vote` comment indicating full integration is pending, but the signing and verification functions are already deployed as production APIs.

### Recommendation

Include `chain_id`, `height`, and `round` in the precommit vote message digest:

```rust
fn build_precommit_vote_message_digest(
    chain_id: &ChainId,
    height: BlockNumber,
    round: Round,
    block_hash: BlockHash,
) -> MessageDigest {
    let chain_id_felt = Felt::try_from(chain_id).expect("chain_id must be valid felt");
    // Use Poseidon over felt-typed fields for canonical domain separation
    MessageDigest(
        HashChain::new()
            .chain(&ascii_as_felt("PRECOMMIT_VOTE").unwrap())
            .chain(&chain_id_felt)
            .chain(&Felt::from(height.0))
            .chain(&Felt::from(round as u64))
            .chain(&block_hash.0)
            .get_poseidon_hash()
    )
}
```

The existing TODO comment at line 122–124 already acknowledges the delimiter-ambiguity concern for this function.

### Proof of Concept

1. Deploy two Starknet sequencer nodes, network A (`chain_id = SN_MAIN`) and network B (`chain_id = SN_FORK`), both initialized from the same genesis block.
2. Feed both networks identical transactions for blocks 1..N so that `calculate_block_hash` produces the same value `H_N` on both.
3. Observe validator V's precommit signature `(r, s)` for `H_N` on network A via the P2P consensus layer.
4. Inject a `Vote { vote_type: Precommit, height: N, round: R, proposal_commitment: H_N, voter: V, signature: (r, s) }` into network B's consensus layer.
5. `verify_precommit_vote_signature(H_N, (r,s), V.public_key)` returns `true` on network B because `build_precommit_vote_message_digest` produces the same digest on both chains.
6. The replayed vote counts toward quorum on network B without V's participation. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L121-124)
```rust
// Utils.
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
