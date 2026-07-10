### Title
Deprecated `verify_transaction_inclusion` Remains Callable, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` (v1) function is still a live, unpermissioned entry point on the NEAR contract. It performs no validation that the caller-supplied `tx_id` is a Merkle leaf (an actual transaction) rather than an internal Merkle tree node. An unprivileged NEAR caller can supply an internal node hash as `tx_id` with a crafted sibling proof and receive a `true` return value for a transaction that does not exist, corrupting the proof result consumed by any downstream contract.

---

### Finding Description

`verify_transaction_inclusion` is annotated `#[deprecated]` and carries an explicit code-level warning:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."

Despite this, the function carries only `#[pause]` — not `#[trusted_relayer]` — meaning any unprivileged NEAR account can call it at any time the contract is unpaused. [1](#0-0) 

The verification logic is:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

`compute_root_from_merkle_proof` simply walks the proof path, hashing left/right according to `current_position % 2`, with no check that `tx_id` is a leaf: [3](#0-2) 

The only guard is `require!(!args.merkle_proof.is_empty())`. There is no guard that `tx_id` is a leaf-level hash.

---

### Impact Explanation

Bitcoin's Merkle tree computes every internal node as `double_sha256(left_child || right_child)`. For a block with transactions `[T1, T2, T3, T4]`, the level-1 internal node is `N = double_sha256(T1 || T2)`. An attacker submits:

- `tx_id = N`
- `tx_index = 0`
- `merkle_proof = [double_sha256(T3 || T4)]`

`compute_root_from_merkle_proof` computes:

```
double_sha256(N || double_sha256(T3||T4))
= double_sha256(double_sha256(T1||T2) || double_sha256(T3||T4))
= actual Merkle root
```

The function returns `true`. The contract has certified that the 64-byte value `N` — which is not a real transaction — is included in the block. Any downstream NEAR contract consuming this result (e.g., to release funds, update state, or authorize an action) is deceived.

The corrupted value is the **proof result** (`bool`) returned by `verify_transaction_inclusion`, which downstream contracts treat as authoritative.

---

### Likelihood Explanation

The attack requires only the public transaction hashes of any confirmed Bitcoin block — information freely available from any block explorer. No privileged access, no key material, no social engineering, and no computational hardness assumption is involved. The attacker constructs the forged call entirely from on-chain public data.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the contract ABI entirely, or add a runtime guard that panics unconditionally (making it a no-op tombstone). A `#[deprecated]` Rust attribute produces only a compile-time warning; it does not restrict NEAR RPC callers. The safe replacement, `verify_transaction_inclusion_v2`, already exists and enforces the coinbase-proof length-equality check that defeats this class of forgery. [4](#0-3) 

---

### Proof of Concept

1. Identify any confirmed Bitcoin block `B` with at least 4 transactions `[T1, T2, T3, T4]`.
2. Compute `N = double_sha256(T1 || T2)` (the level-1 internal node).
3. Compute `S = double_sha256(T3 || T4)` (the sibling subtree hash).
4. Call `verify_transaction_inclusion` on the NEAR contract with:
   - `tx_id = N`
   - `tx_block_blockhash = hash(B)`
   - `tx_index = 0`
   - `merkle_proof = [S]`
   - `confirmations = 1`
5. The contract returns `true`. No such transaction `N` exists in block `B`.

The root cause is the absence of any input validation bounding `tx_id` to the leaf level of the Merkle tree, directly analogous to the external report's finding of insufficient filtering of attacker-controlled inputs before they reach a security-critical decision point. [5](#0-4) [2](#0-1)

### Citations

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-323)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-369)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** merkle-tools/src/lib.rs (L34-52)
```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;

    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
}
```

**File:** merkle-tools/src/lib.rs (L54-60)
```rust
fn compute_hash(first_tx_hash: &H256, second_tx_hash: &H256) -> H256 {
    let mut concat_inputs = Vec::with_capacity(64);
    concat_inputs.extend(first_tx_hash.0);
    concat_inputs.extend(second_tx_hash.0);

    double_sha256(&concat_inputs)
}
```
