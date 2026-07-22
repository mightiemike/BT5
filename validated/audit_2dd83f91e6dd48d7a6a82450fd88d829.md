### Title
Missing Vote Signature Verification Allows Any Peer to Forge Consensus Votes as Any Validator - (File: crates/apollo_consensus/src/single_height_consensus.rs)

### Summary
`SingleHeightConsensus::handle_vote` accepts and counts votes from any network peer claiming to be any committee validator without verifying the cryptographic signature on the vote. A single malicious peer can forge quorum-weight votes for arbitrary validators, manipulating the BFT consensus engine into committing a block it should not commit.

### Finding Description

`handle_vote` in `single_height_consensus.rs` performs two checks before accepting a vote:

1. The vote's `height` field matches the current height.
2. The `vote.voter` address appears in the committee member list.

It does **not** verify that the sender actually controls the private key corresponding to `vote.voter`. The TODO comment at line 242 explicitly acknowledges this gap:

```rust
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // TODO(Asmaa): verify the signature
    trace!("Received {:?}", vote);
    ...
    if !self.committee.members().iter().any(|s| s.address == vote.voter) {
        debug!("Ignoring vote from non validator: vote={:?}", vote);
        return VecDeque::new();
    }
    // proceeds to count the vote
``` [1](#0-0) 

Votes are created with `RawSignature::default()` (a zero/empty signature) and are never signed before broadcast:

```rust
let vote = Vote {
    ...
    // TODO(Asmaa): sign the vote
    signature: RawSignature::default(),
};
``` [2](#0-1) 

The `SignatureManager` already exposes `verify_precommit_vote_signature` and `verify_identity` for exactly this purpose, but neither is called anywhere in the vote-handling path. [3](#0-2) 

The `build_precommit_vote_message_digest` function and the full ECDSA verification infrastructure exist and are tested in isolation, but are never wired into `handle_vote`. [4](#0-3) 

### Impact Explanation

An unprivileged network peer that is allowed to send gossip messages (i.e., any peer reachable by the node) can:

1. Craft a `Vote` struct with `voter` set to any committee validator address and `proposal_commitment` set to any block hash.
2. Broadcast it. The receiving node's `handle_vote` will accept it as a legitimate vote from that validator.
3. By sending forged votes for enough committee members to exceed the quorum threshold, the attacker drives the state machine to `DecisionReached` for an arbitrary `ProposalCommitment`.

This maps directly to the external bug's pattern: just as `mint_stable_coin` was callable by anyone pretending to be the collateral contract, `handle_vote` accepts votes from anyone pretending to be any validator. The result is that the consensus layer can be forced to commit a block that was never legitimately proposed or validated, causing the sequencer to produce an invalid state transition.

**Impact category:** Critical — invalid block accepted through consensus signature logic, leading to wrong state, receipts, and events from blockifier execution of that block.

### Likelihood Explanation

Any peer that can reach the node's gossip network can trigger this. No special privilege, stake, or cryptographic material is required. The attacker only needs to know the committee member addresses (which are public) and the current block height. The attack is trivially automatable.

### Recommendation

Wire `verify_precommit_vote_signature` (or an equivalent per-vote-type verifier) into `handle_vote` before the vote is forwarded to the state machine. Specifically:

- Sign votes in `make_self_vote` using `SignatureManager::sign_precommit_vote` (removing the `TODO(Asmaa): sign the vote` placeholder).
- In `handle_vote`, look up the public key for `vote.voter` from the committee and call `verify_precommit_vote_signature(block_hash, vote.signature, public_key)`. Reject the vote if verification fails.
- Apply the same fix to prevotes (which share the same unsigned path through `make_self_vote`). [5](#0-4) 

### Proof of Concept

```
1. Attacker connects to a sequencer node as a libp2p peer.
2. Attacker observes the current committee (public information) and current height H.
3. Attacker constructs:
     Vote {
         vote_type: VoteType::Precommit,
         height: H,
         round: 0,
         proposal_commitment: Some(attacker_chosen_block_hash),
         voter: <any_committee_member_address>,
         signature: RawSignature::default(),   // zero bytes, never checked
     }
4. Attacker broadcasts this vote for each committee member address until quorum weight is reached.
5. handle_vote passes the height check and the committee membership check for each forged vote.
6. The state machine accumulates quorum weight and emits SMRequest::DecisionReached with
   attacker_chosen_block_hash.
7. The sequencer commits a block it never validated, producing wrong state/receipts/events.
``` [6](#0-5)

### Citations

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L239-281)
```rust
    /// Handle vote messages from peer nodes.
    #[instrument(skip_all)]
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
        trace!("Received {:?}", vote);
        let height = self.state_machine.height();
        if vote.height != height {
            warn!("Invalid vote height: expected {:?}, got {:?}", height, vote.height);
            return VecDeque::new();
        }
        if !self.committee.members().iter().any(|s| s.address == vote.voter) {
            debug!("Ignoring vote from non validator: vote={:?}", vote);
            return VecDeque::new();
        }

        // Check if vote has already been received.
        match self.state_machine.received_vote(&vote) {
            VoteStatus::Duplicate => {
                // Duplicate - ignore.
                trace_every_n_ms!(
                    DUPLICATE_VOTE_LOG_PERIOD_MS,
                    "Ignoring duplicate vote: {vote:?}"
                );
                return VecDeque::new();
            }
            VoteStatus::Conflict(old_vote, new_vote) => {
                // Conflict - ignore and record.
                warn!("Conflicting votes: old={old_vote:?}, new={new_vote:?}");
                CONSENSUS_CONFLICTING_VOTES.increment(1);
                return VecDeque::new();
            }
            VoteStatus::New => {
                // Vote is new, proceed to process it.
            }
        }

        info!("Accepting {:?}", vote);
        let sm_vote = match vote.vote_type {
            VoteType::Prevote => StateMachineEvent::Prevote(vote),
            VoteType::Precommit => StateMachineEvent::Precommit(vote),
        };
        self.state_machine.handle_event(sm_vote)
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

**File:** crates/apollo_consensus/src/state_machine.rs (L694-716)
```rust
    fn upon_decision(&mut self, round: u32) -> VecDeque<SMRequest> {
        let Some((Some(proposal_id), _)) = self.proposals.get(&round) else {
            return VecDeque::new();
        };
        if !self.value_has_enough_votes(&self.precommits, round, &Some(*proposal_id), &self.quorum)
        {
            return VecDeque::new();
        }
        if !self.virtual_proposer_in_favor(&self.precommits, round, &Some(*proposal_id)) {
            return VecDeque::new();
        }
        // Collect all supporting precommits for this proposal and round.
        let supporting_precommits: Vec<Vote> = self
            .precommits
            .iter()
            .filter(|(&(r, _voter), (v, _w))| {
                r == round && v.proposal_commitment == Some(*proposal_id)
            })
            .map(|(_vote_key, (v, _w))| v.clone())
            .collect();

        let decision = Decision { precommits: supporting_precommits, block: *proposal_id, round };
        VecDeque::from([SMRequest::DecisionReached(decision)])
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
