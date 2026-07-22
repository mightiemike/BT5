The evidence is conclusive. Let me lay out the full chain.

### Title
Unsigned Vote Injection via `unwrap_or_default()` Bypasses Consensus Quorum — (`crates/apollo_consensus/src/single_height_consensus.rs`)

### Summary

A network peer can forge votes for any committee member by sending a `protobuf::Vote` with `signature=None`. The protobuf converter silently substitutes an empty `RawSignature`, and `handle_vote` contains an explicit unimplemented signature check (`// TODO(Asmaa): verify the signature`). The vote is accepted and counted toward quorum with no cryptographic verification.

---

### Finding Description

**Step 1 — Silent empty-signature substitution in the protobuf converter** [1](#0-0) 

```rust
// Convert Hashes to RawSignature (default to empty if None)
let signature =
    value.signature.map(|hashes| hashes.try_into()).transpose()?.unwrap_or_default();
```

When `protobuf::Vote.signature` is `None`, `unwrap_or_default()` produces `RawSignature(vec![])` — an empty signature — with no error and no rejection.

**Step 2 — Signature verification is explicitly absent in `handle_vote`** [2](#0-1) 

```rust
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // TODO(Asmaa): verify the signature   ← verification never happens
    ...
    if !self.committee.members().iter().any(|s| s.address == vote.voter) {
        debug!("Ignoring vote from non validator: vote={:?}", vote);
        return VecDeque::new();
    }
```

The only guards are a height check and a committee-membership check on `vote.voter`. The `voter` field is fully attacker-controlled (it is a plain `ContractAddress` decoded from the wire message, not bound to the sender's network identity). No call to `verify_precommit_vote_signature` or any equivalent is made.

**Step 3 — The vote is inserted directly into the quorum-counting map** [3](#0-2) 

```rust
fn handle_precommit(&mut self, vote: Vote) -> VecDeque<SMRequest> {
    let round = vote.round;
    let voter = vote.voter;
    let inserted =
        self.precommits.insert((round, voter), (vote, self.vote_weight(voter))).is_none();
    ...
    self.map_round_to_upons(round)
}
```

The vote is stored and its weight counted toward quorum with no signature check at any layer.

**Step 4 — `verify_precommit_vote_signature` exists but is never called on the inbound path** [4](#0-3) 

The function `verify_precommit_vote_signature` is defined and tested in isolation, but is never invoked from `handle_vote` or anywhere in the `SingleHeightConsensus` / `MultiHeightManager` inbound vote path.

---

### Impact Explanation

An attacker who is a connected P2P peer (no validator key required) can:

1. Enumerate the current committee (public information).
2. For each committee member `V_i`, send a `protobuf::Vote` with `voter = V_i`, `signature = None`, `vote_type = Precommit`, and `proposal_commitment = <attacker-chosen hash>`.
3. Each message deserializes successfully with an empty `RawSignature`.
4. Each passes the committee-membership check because `voter` is a valid committee address.
5. Each is inserted into the precommit map with the member's full stake weight.
6. Once enough forged votes accumulate, `upon_decision` fires and `DecisionReached` is emitted for the attacker-chosen block commitment. [5](#0-4) 

This constitutes a consensus safety violation: an honest node commits a block it never validated, leading to wrong state, receipts, and events being persisted.

---

### Likelihood Explanation

The attack requires only network connectivity to the sequencer's P2P port. No private key, no stake, no operator access. The `TODO` comment confirms the check is known to be missing and has not been implemented. The duplicate-vote guard (`VoteStatus::Duplicate`) only prevents the same `(round, voter)` pair from being counted twice, which does not impede the attack since the attacker sends one forged vote per distinct committee member.

---

### Recommendation

1. **Immediately implement signature verification in `handle_vote`** before the committee-membership check. Call `verify_precommit_vote_signature` (or the prevote equivalent) using the voter's registered public key from the committee.
2. **Reject votes with empty `RawSignature`** at the protobuf boundary rather than silently defaulting.
3. **Make `signature` a required field** in the protobuf schema (remove the `Option` wrapping) so that a missing signature is a parse error, not a silent default.

---

### Proof of Concept

```rust
// Serialize a Vote with signature=None
let proto_vote = protobuf::Vote {
    vote_type: protobuf::vote::VoteType::Precommit as i32,
    height: 42,
    round: 0,
    proposal_commitment: Some(attacker_chosen_hash.into()),
    voter: Some(committee_member_address.into()),
    signature: None,   // ← omit signature field
};
let bytes: Vec<u8> = proto_vote.into();

// Deserialize via TryFrom — succeeds, signature is RawSignature([])
let vote: Vote = bytes.try_into().unwrap();
assert!(vote.signature.0.is_empty());

// Send N such messages (one per committee member) over the broadcast channel.
// handle_vote accepts each; upon_decision fires for the attacker-chosen commitment.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_protobuf/src/converters/consensus.rs (L72-92)
```rust
impl TryFrom<protobuf::Vote> for Vote {
    type Error = ProtobufConversionError;

    fn try_from(value: protobuf::Vote) -> Result<Self, Self::Error> {
        let vote_type = protobuf::vote::VoteType::try_from(value.vote_type)?.try_into()?;

        let height = BlockNumber(value.height);
        let round = value.round;
        let proposal_commitment: Option<ProposalCommitment> = value
            .proposal_commitment
            .map(|proposal_commitment| proposal_commitment.try_into())
            .transpose()?
            .map(ProposalCommitment);
        let voter = value.voter.ok_or(missing("voter"))?.try_into()?;
        // Convert Hashes to RawSignature (default to empty if None)
        let signature =
            value.signature.map(|hashes| hashes.try_into()).transpose()?.unwrap_or_default();

        Ok(Vote { vote_type, height, round, proposal_commitment, voter, signature })
    }
}
```

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L241-281)
```rust
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

**File:** crates/apollo_consensus/src/state_machine.rs (L440-450)
```rust
    fn handle_precommit(&mut self, vote: Vote) -> VecDeque<SMRequest> {
        let round = vote.round;
        let voter = vote.voter;
        let inserted =
            self.precommits.insert((round, voter), (vote, self.vote_weight(voter))).is_none();
        assert!(
            inserted,
            "SHC should handle conflicts & replays: duplicate precommit for round={round}, \
             voter={voter}"
        );
        self.map_round_to_upons(round)
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
