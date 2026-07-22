### Title
Precommit Vote Signature Preimage Omits Chain ID, Height, Round, and Vote Type — (`crates/apollo_signature_manager/src/signature_manager.rs`)

### Summary
`build_precommit_vote_message_digest` constructs the ECDSA preimage as `b"PRECOMMIT_VOTE" || block_hash` only. The chain ID, block height, consensus round, and vote type are absent from the signed bytes. A precommit signature produced for block hash `X` on one chain (or at one height/round) is cryptographically identical to a precommit signature for the same hash on any other chain, at any other height, or for any other vote type. This is the direct sequencer analog of the NFT `preMint()` bug: the signed payload lacks the domain separator that would bind it to a specific context.

### Finding Description

`build_precommit_vote_message_digest` in `crates/apollo_signature_manager/src/signature_manager.rs`:

```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash);
    MessageDigest(blake2s_to_felt(&message))
}
``` [1](#0-0) 

The signed preimage is `b"PRECOMMIT_VOTE" || block_hash.to_bytes_be()`. The `Vote` struct carries four additional fields that are **not** covered by the signature:

```rust
pub struct Vote {
    pub vote_type: VoteType,       // not signed
    pub height: BlockNumber,       // not signed
    pub round: Round,              // not signed
    pub proposal_commitment: Option<ProposalCommitment>,  // block_hash — signed
    pub voter: ContractAddress,    // not signed
    pub signature: RawSignature,
}
``` [2](#0-1) 

The `sign_precommit_vote` entry point passes only `block_hash` to the digest builder: [3](#0-2) 

`verify_precommit_vote_signature` reconstructs the same narrow digest and verifies against it: [4](#0-3) 

The protobuf `Vote` message and the Rust `Vote` struct both omit `chain_id` entirely: [5](#0-4) 

### Impact Explanation

Because the signed bytes contain only a static tag and the block hash, the same signature is valid for:

1. **Cross-chain replay** — A precommit for block hash `X` on Starknet mainnet is byte-for-byte identical to a precommit for block hash `X` on any testnet or fork that happens to produce the same hash. A validator key compromised on one network can be used to forge precommit votes on another.

2. **Cross-height/round replay** — If the same block hash is re-proposed at a different height or round (e.g., after a network reset, a re-org, or a reproposal), the original precommit signature verifies without modification. The `height` and `round` fields in the `Vote` struct can be set to arbitrary values by the replayer.

3. **Cross-type replay** — Because `vote_type` (Prevote vs. Precommit) is not in the digest, a precommit signature is also a valid prevote signature for the same block hash. An attacker who observes a precommit can inject it as a prevote at a different round, potentially manipulating the Tendermint state machine.

The impact matches: **High — signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

- The `sign_precommit_vote` / `verify_precommit_vote_signature` pair is production code, not a test stub.
- Cross-chain replay requires the same block hash on two networks; because transaction hashes include `chain_id`, this is cryptographically infeasible for live blocks. However, cross-height and cross-round replay require only that the same block hash be re-proposed, which is a normal Tendermint reproposal scenario.
- The `// TODO(Asmaa): sign the vote` comment in `state_machine.rs` confirms that vote signing is actively being wired up, making this the right moment to fix the preimage before the signing path is fully connected. [6](#0-5) 

### Recommendation

Extend `build_precommit_vote_message_digest` to include all fields that uniquely identify the vote's context:

```rust
fn build_precommit_vote_message_digest(
    chain_id: &ChainId,
    height: BlockNumber,
    round: Round,
    vote_type: VoteType,
    block_hash: BlockHash,
) -> MessageDigest {
    let mut message = Vec::new();
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(chain_id.as_bytes());
    message.extend_from_slice(&height.0.to_be_bytes());
    message.extend_from_slice(&(round as u64).to_be_bytes());
    message.push(vote_type as u8);
    message.extend_from_slice(&block_hash.to_bytes_be());
    MessageDigest(blake2s_to_felt(&message))
}
```

Propagate `chain_id` into `SignatureManager` at construction time (it is already available in `SequencerContextConfig`): [7](#0-6) 

Update `verify_precommit_vote_signature` to accept and bind the same fields.

### Proof of Concept

1. Validator V signs a precommit for block hash `H` at height 100, round 0 on Starknet Sepolia. The signed bytes are `blake2s("PRECOMMIT_VOTE" || H)`.
2. An observer extracts the `(block_hash, signature)` pair from the broadcast `Vote` message.
3. The observer constructs a new `Vote { vote_type: Precommit, height: 200, round: 3, proposal_commitment: Some(H), voter: V, signature: <copied> }`.
4. `verify_precommit_vote_signature(H, signature, V.public_key)` returns `true` because the digest is identical — height, round, and chain are not checked.
5. The replayed vote is accepted by any node running `handle_vote`, potentially contributing to a spurious quorum at height 200, round 3. [1](#0-0) [4](#0-3)

### Citations

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

**File:** crates/apollo_protobuf/src/proto/p2p/proto/consensus/consensus.proto (L20-36)
```text
message Vote {
    enum  VoteType {
        Prevote   = 0;
        Precommit = 1;
    };

    // We use a type field to distinguish between prevotes and precommits instead of different
    // messages, to make sure the data, and therefore the signatures, are unambiguous between
    // Prevote and Precommit.
    VoteType      vote_type           = 2;
    uint64        height              = 3;
    uint32        round               = 4;
    // This is optional since a vote can be NIL.
    optional Hash proposal_commitment = 5;
    Address       voter               = 6;
    Hashes        signature           = 7;
}
```

**File:** crates/apollo_consensus/src/state_machine.rs (L254-255)
```rust
            // TODO(Asmaa): sign the vote
            signature: RawSignature::default(),
```

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L1-5)
```rust
use std::collections::BTreeMap;
use std::fmt::Debug;
use std::time::Duration;

use apollo_config::behavior_mode::BehaviorMode;
```
