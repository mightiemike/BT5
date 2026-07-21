### Title
Delimiter-Free Concatenation in `build_peer_identity_message_digest` Enables Cross-Identity Signature Reuse — (`crates/apollo_signature_manager/src/signature_manager.rs`)

---

### Summary

`build_peer_identity_message_digest` concatenates a domain prefix, a variable-length `peer_id` byte slice, and a variable-length `challenge` byte slice with no length delimiters. Because the boundary between the two variable-length fields is not encoded, two distinct `(peer_id_A, challenge_X)` pairs can produce an identical byte string — and therefore an identical Blake2s digest — as a different `(peer_id_B, challenge_Y)` pair. A valid ECDSA signature obtained for one pair is cryptographically valid for the other, allowing an attacker to authenticate as a peer identity they do not control.

The codebase itself acknowledges the problem: a TODO comment at the exact site of the bug reads *"Consider wrapping each field in fixed delimiters (e.g. parentheses or tags) to avoid delimiter ambiguity across implementations; see apollo_propeller/signature.rs and PR review."*

---

### Finding Description

`build_peer_identity_message_digest` in `crates/apollo_signature_manager/src/signature_manager.rs` constructs the signed payload as:

```
INIT_PEER_ID  ||  peer_id.to_bytes()  ||  challenge.0
   (12 B, fixed)    (variable length)     (variable length)
``` [1](#0-0) 

Because no length field or delimiter separates `peer_id.to_bytes()` from `challenge.0`, the concatenation is ambiguous. Any split of the combined byte string `peer_id.to_bytes() || challenge.0` into two parts yields a valid alternative `(peer_id', challenge')` pair that hashes to the same digest.

In libp2p, `PeerId::to_bytes()` serialises the peer as a multihash. The byte length varies by key type:

- **Ed25519 / secp256k1 (identity multihash):** length is determined by the protobuf-encoded public key, typically 36–40 bytes.
- **RSA (SHA-256 multihash):** always 34 bytes.

An attacker who controls their own keypair therefore controls the length of their own `peer_id.to_bytes()`. By choosing a key type whose serialised peer-id length differs from a target peer's by `k` bytes, the attacker can shift `k` bytes from the tail of their peer-id into the head of the challenge, producing a collision whenever the verifier issues a challenge whose first `k` bytes match those tail bytes. [2](#0-1) 

The companion function `build_precommit_vote_message_digest` is **not** affected: `block_hash.to_bytes_be()` is always 32 bytes, so the concatenation is unambiguous. [3](#0-2) 

The propeller `build_signed_payload` is also safe because every field (`MessageRoot`, `CommitteeId`, `nonce`) is fixed-width. [4](#0-3) 

---

### Impact Explanation

`verify_identity` is the public entry point that calls `build_peer_identity_message_digest` and then verifies the ECDSA signature against the resulting digest. [5](#0-4) 

If an attacker can present a signature that passes `verify_identity` for a `peer_id` they do not own, they can impersonate a legitimate validator or sequencer node in the P2P/consensus layer. This maps to the **High** impact category: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

---

### Likelihood Explanation

Exploitation requires the attacker to:

1. Choose a key type whose `peer_id.to_bytes()` length differs from the target's by `k` bytes.
2. Obtain a valid signature for their own `(peer_id_A, challenge_X)` — trivial, since they hold the private key.
3. Wait for (or influence) the verifier to issue a challenge `challenge_Y` to the target `peer_id_B` such that `challenge_Y` starts with the `k`-byte suffix of `peer_id_A.to_bytes()`.

Step 3 is the limiting factor. If challenges are uniformly random, the probability of a natural collision for a given attempt is `2^{-8k}`. However, if the challenge length is short, or if the attacker can make repeated attempts, the window widens. The acknowledged TODO comment indicates the developers consider this a real concern, not a theoretical one.

---

### Recommendation

Replace the bare concatenation with a length-prefixed or structurally unambiguous encoding. Two options:

**Option A — length-prefix each variable-length field:**
```rust
fn build_peer_identity_message_digest(peer_id: PeerId, challenge: Challenge) -> MessageDigest {
    let peer_id_bytes = peer_id.to_bytes();
    let challenge_bytes = &challenge.0;
    let mut message = Vec::new();
    message.extend_from_slice(INIT_PEER_ID);
    message.extend_from_slice(&(peer_id_bytes.len() as u64).to_be_bytes());
    message.extend_from_slice(&peer_id_bytes);
    message.extend_from_slice(&(challenge_bytes.len() as u64).to_be_bytes());
    message.extend_from_slice(challenge_bytes);
    MessageDigest(blake2s_to_felt(&message))
}
```

**Option B — use a structured hash (e.g., Poseidon over fixed-size field elements)**, consistent with how the rest of the sequencer hashes structured data.

The propeller module's approach of wrapping with a fixed prefix **and** postfix (`<propeller>...</propeller>`) is a partial mitigation but does not eliminate intra-domain ambiguity when multiple variable-length fields are present. [6](#0-5) 

---

### Proof of Concept

```
INIT_PEER_ID = b"INIT_PEER_ID"   # 12 bytes

# Legitimate peer A (RSA key → 34-byte peer_id)
peer_id_A  = [0x01] * 34
challenge_X = [0xAA, 0xBB, 0xCC, 0xDD, 0xEE]

# Attacker crafts peer_id_B (Ed25519 key → 39-byte peer_id)
# by choosing peer_id_B such that:
#   peer_id_B[0:34] == peer_id_A
#   peer_id_B[34:39] == challenge_X[0:5]
peer_id_B  = [0x01] * 34 + [0xAA, 0xBB, 0xCC, 0xDD, 0xEE]
challenge_Y = challenge_X[5:]   # remaining bytes

# Concatenations are identical:
# INIT_PEER_ID || peer_id_A || challenge_X
# == INIT_PEER_ID || peer_id_B || challenge_Y
# → same Blake2s digest → same ECDSA signature is valid for both
```

A signature produced by peer A over `(peer_id_A, challenge_X)` passes `verify_identity` for `(peer_id_B, challenge_Y)` without the attacker holding peer B's private key. [1](#0-0)

### Citations

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L122-136)
```rust
// TODO(noam.s): Consider wrapping each field in fixed delimiters (e.g. parentheses or tags) to
// avoid delimiter ambiguity across implementations; see apollo_propeller/signature.rs and PR
// review.
// TODO(noam.s): replace peer_id with staker_address (or add a new
// build_staker_identity_message_digest function)
fn build_peer_identity_message_digest(peer_id: PeerId, challenge: Challenge) -> MessageDigest {
    let challenge = &challenge.0;
    let peer_id = peer_id.to_bytes();
    let mut message = Vec::with_capacity(INIT_PEER_ID.len() + peer_id.len() + challenge.len());
    message.extend_from_slice(INIT_PEER_ID);
    message.extend_from_slice(&peer_id);
    message.extend_from_slice(challenge);

    MessageDigest(blake2s_to_felt(&message))
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

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L169-177)
```rust
pub fn verify_identity(
    peer_id: PeerId,
    challenge: Challenge,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> {
    let message_digest = build_peer_identity_message_digest(peer_id, challenge);
    verify_signature(message_digest, signature, public_key)
}
```

**File:** crates/apollo_propeller/src/signature.rs (L10-12)
```rust
// TODO(AndrewL): Consider removing these (consult gossipsub code )
pub const SIGNING_PREFIX: &[u8] = b"<propeller>";
pub const SIGNING_POSTFIX: &[u8] = b"</propeller>";
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
