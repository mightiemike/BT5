### Title
Unvalidated `coinbase_tx_id` in `verify_transaction_inclusion_v2` Allows 64-Byte Merkle Proof Forgery Bypass — (`File: contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` accepts a caller-supplied `coinbase_tx_id` without validating that it is the actual coinbase transaction of the block. The only check performed is that `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == merkle_root`. Because internal Merkle tree nodes also satisfy this equation (when positioned at index 0 of a subtree), an attacker can supply an internal node as `coinbase_tx_id` with a shortened proof, then prove inclusion of another internal node as the "target transaction." This completely bypasses the 64-byte transaction forgery mitigation the function was designed to enforce.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to fix the known 64-byte transaction Merkle proof forgery vulnerability present in the deprecated `verify_transaction_inclusion`. The intended mitigation is: require a coinbase proof of the same length as the target tx proof, so that the target tx must be at the same tree depth as the coinbase (always a real leaf at full depth L).

The implementation performs three checks:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` — lengths must match.
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == merkle_root` — coinbase proof must be valid.
3. Delegates to `verify_transaction_inclusion` which checks `compute_root_from_merkle_proof(tx_id, tx_index, &merkle_proof) == merkle_root`. [1](#0-0) 

The critical missing check: **`coinbase_tx_id` is never validated to be the actual coinbase transaction**. It is entirely caller-supplied. The only constraint is that it produces the correct Merkle root when treated as a leaf at position 0 with the provided `coinbase_merkle_proof`.

In a Bitcoin Merkle tree of depth L, the leftmost internal node at any depth D < L is the root of the left subtree at that level. This node has a valid Merkle proof of length D to the block root, with position always 0 at every step (since `current_position /= 2` starting from 0 stays 0). Therefore, any internal node at depth D, position 0 satisfies check (2) with a proof of length D. [2](#0-1) 

The `coinbase_merkle_proof` elements are never cross-validated against `merkle_proof` elements. The two proofs are completely independent — they only share a length constraint. [3](#0-2) 

---

### Impact Explanation

A consumer contract calling `verify_transaction_inclusion_v2` receives `true` for a `tx_id` that is an internal Merkle tree node, not a real Bitcoin transaction. This is precisely the 64-byte forgery the function was designed to prevent. An attacker can fabricate proof of a Bitcoin payment that never occurred, enabling cross-chain fraud: e.g., claiming a bridged asset release, triggering an atomic swap settlement, or satisfying any on-chain condition gated on Bitcoin transaction inclusion.

The corrupted invariant is the proof result returned by `verify_transaction_inclusion_v2`: it returns `true` for a non-leaf (non-transaction) hash, breaking the guarantee that a `true` result corresponds to a real confirmed Bitcoin transaction.

---

### Likelihood Explanation

`verify_transaction_inclusion_v2` is a public, unpermissioned method (only `#[pause]`, no `#[trusted_relayer]` or role gate). [4](#0-3) 

Any NEAR account can call it. The attacker needs only public information: the Merkle tree of any mainchain block (available from any Bitcoin full node or block explorer). No privileged access, leaked keys, or social engineering is required. The attack is deterministic and requires no brute force.

---

### Recommendation

The `coinbase_tx_id` must be validated to be the actual coinbase transaction. Since the contract stores only the block header (not individual txids), this cannot be done by direct lookup. The correct mitigation is to cross-validate the two proof paths: at each level of the tree, the sibling node in `coinbase_merkle_proof[i]` must equal the sibling node in `merkle_proof[i]` whenever both proofs traverse the same parent node. Concretely, for every level `i`, if `(coinbase_position >> i) == (tx_position >> i)` (same subtree), the sibling hashes must match. This ensures the coinbase and target tx are in the same real tree, preventing use of a detached internal-node proof as a fake coinbase anchor.

---

### Proof of Concept

Consider a mainchain block B with 4 transactions `T0, T1, T2, T3` and Merkle tree:

```
root = H(H(T0,T1), H(T2,T3))
  depth-1 nodes: N_L = H(T0,T1)  [pos 0],  N_R = H(T2,T3)  [pos 1]
  depth-2 leaves: T0[pos 0], T1[pos 1], T2[pos 2], T3[pos 3]
```

Real coinbase proof (T0 at pos 0, length 2): `[T1, N_R]`

**Attacker's forged call to `verify_transaction_inclusion_v2`:**

```
tx_id                = N_L  (= H(T0,T1), an internal node — not a real tx)
tx_block_blockhash   = hash(B)
tx_index             = 0
merkle_proof         = [N_R]          // length 1
coinbase_tx_id       = N_L            // same internal node, NOT the real coinbase T0
coinbase_merkle_proof= [N_R]          // length 1
confirmations        = 1
```

**Checks performed by the contract:**

1. `merkle_proof.len() == coinbase_merkle_proof.len()` → `1 == 1` ✓
2. `compute_root_from_merkle_proof(N_L, 0, [N_R])` = `H(N_L, N_R)` = `root` ✓
3. `compute_root_from_merkle_proof(N_L, 0, [N_R])` = `root` ✓

`verify_transaction_inclusion_v2` returns **`true`** for `tx_id = N_L`, which is an internal node, not a real Bitcoin transaction. The 64-byte forgery mitigation is fully bypassed. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L317-322)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
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

**File:** btc-types/src/contract_args.rs (L26-36)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
