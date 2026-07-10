### Title
Missing `tx_index != 0` guard in `verify_transaction_inclusion_v2` allows 64-byte Merkle forgery bypass - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced specifically to defeat the 64-byte transaction Merkle-proof forgery attack by requiring a coinbase proof of equal length. The protection relies on the coinbase proof independently establishing the true tree depth. However, when a caller sets `tx_index = 0`, they can supply the same internal Merkle-tree node as both `coinbase_tx_id` and `tx_id`, with the same shortened proof path. Both the coinbase check and the transaction check pass, and the function returns `true` for a transaction that was never included in the block.

---

### Finding Description

The protection added in v2 is:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` — equal depth
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == merkle_root` — coinbase proof is valid

The invariant that makes this work is: the coinbase proof is independently constrained (the real coinbase is at index 0 with a fixed, full-depth path), so it forces `merkle_proof` to also be full-depth, preventing a shorter path to an internal node from being used for `tx_id`.

That invariant breaks when `tx_index == 0`. In that case the attacker sets:

```
tx_id              = <internal node N at tree level k>
tx_index           = 0
merkle_proof       = <shortened proof of length k>
coinbase_tx_id     = <same internal node N>
coinbase_merkle_proof = <same shortened proof of length k>
```

Both proofs are identical and both verify to the block's `merkle_root`. No check distinguishes the coinbase from the target transaction. The required guard — that `tx_index != 0` (the coinbase slot cannot simultaneously be the forged transaction) — is never performed.

The relevant code path: [1](#0-0) 

The length equality check passes trivially (both proofs are the same length), and the coinbase `require!` passes because the internal node with the shortened proof does hash to the merkle root: [2](#0-1) 

Then the deprecated v1 function is called, which also passes because it performs the identical computation with the same inputs: [3](#0-2) 

The `compute_root_from_merkle_proof` function in `merkle-tools` has no awareness of tree depth or node level — it simply hashes upward from whatever starting hash is given: [4](#0-3) 

---

### Impact Explanation

Any downstream contract or bridge that calls `verify_transaction_inclusion_v2` and acts on a `true` result (e.g., releasing funds, minting wrapped tokens, or recording a cross-chain event) can be deceived into accepting a Bitcoin transaction that never existed. The forged `tx_id` is an internal Merkle-tree node, not a real transaction hash. The contract's own documentation acknowledges that distinguishing internal nodes from real transactions is left to the caller, but the v2 function is explicitly advertised as mitigating the 64-byte forgery — a mitigation that is bypassable here.

---

### Likelihood Explanation

The attacker is an unprivileged NEAR caller with no special role. They need only:

1. A mainchain block with at least 4 transactions (so a non-trivial internal node exists at level 1).
2. Knowledge of two adjacent transaction hashes `T0`, `T1` in that block (publicly available from any Bitcoin node).
3. The ability to call `verify_transaction_inclusion_v2` with crafted arguments.

All three conditions are trivially satisfiable on any live deployment. The 64-byte forgery has been demonstrated in practice against real SPV implementations.

---

### Recommendation

Add an explicit guard at the top of `verify_transaction_inclusion_v2`:

```rust
require!(args.tx_index != 0, "tx_index 0 is reserved for the coinbase transaction");
```

Alternatively, require that `args.tx_id != args.coinbase_tx_id`, which catches the same case without restricting coinbase verification entirely. The coinbase proof's purpose is to serve as an *independent* depth witness; if it is identical to the transaction proof, it provides no additional constraint.

---

### Proof of Concept

Given a mainchain Bitcoin block `B` with 4 transactions `[T0, T1, T2, T3]`:

```
Level 1:  N01 = hash(T0 ‖ T1),   N23 = hash(T2 ‖ T3)
Root:     R   = hash(N01 ‖ N23)
```

Call `verify_transaction_inclusion_v2` with:

```
tx_id                 = N01          // internal node, not a real transaction
tx_block_blockhash    = hash(B)
tx_index              = 0
merkle_proof          = [N23]        // length 1, shortened path
coinbase_tx_id        = N01          // same internal node
coinbase_merkle_proof = [N23]        // same shortened path
confirmations         = 1
```

Step-by-step execution:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` → `1 == 1` ✓
2. `compute_root_from_merkle_proof(N01, 0, [N23])` → `hash(N01, N23) = R == merkle_root` ✓
3. v1 called: `compute_root_from_merkle_proof(N01, 0, [N23])` → `R == merkle_root` ✓
4. Returns **`true`** — a forged inclusion proof accepted.

### Citations

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
