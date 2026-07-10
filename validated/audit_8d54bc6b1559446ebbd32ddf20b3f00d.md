### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle Tree Nodes as Valid Transaction IDs, Enabling SPV Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function remains a live, publicly callable NEAR entry point. It does not validate that the caller-supplied `tx_id` is a leaf node (a real Bitcoin transaction hash) rather than an internal Merkle tree node. An unprivileged NEAR caller can supply a crafted `tx_id` equal to any internal node of a real block's Merkle tree, paired with a valid proof from that node to the root, and the function returns `true` — falsely certifying the inclusion of a transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` in `contract/src/lib.rs` verifies SPV inclusion by computing the Merkle root from the caller-supplied `tx_id`, `tx_index`, and `merkle_proof`, then comparing the result to the stored `block_header.merkle_root`: [1](#0-0) 

The function performs no check that `tx_id` is a leaf node. In Bitcoin's Merkle tree, every internal node is itself a valid 32-byte hash: `double_sha256(left || right)`. An attacker who knows the Merkle tree of any block already on the mainchain can:

1. Pick any internal node `N` at depth `d` (where `d < tree_depth`).
2. Construct a valid Merkle proof of length `tree_depth − d` from `N` up to the root.
3. Call `verify_transaction_inclusion` with `tx_id = N`, the correct `tx_index` for that node's position, and the proof.

`compute_root_from_merkle_proof` will faithfully recompute the correct root and the function returns `true`: [2](#0-1) 

The contract's own docstring acknowledges this gap explicitly: [3](#0-2) 

The `#[deprecated]` Rust attribute is a **compile-time** warning only. It does not restrict runtime invocation. Any NEAR account can call this method via a signed transaction regardless of the attribute. The `#[pause]` guard only applies when the contract is administratively paused — under normal operation the function is fully reachable: [4](#0-3) 

`verify_transaction_inclusion_v2` was introduced to mitigate this by requiring a coinbase proof of the same length, which pins the tree depth and prevents internal-node substitution: [5](#0-4) 

However, v1 was never removed or access-restricted, so the mitigation is bypassable by calling v1 directly.

---

### Impact Explanation

Any downstream NEAR contract or off-chain application that calls `verify_transaction_inclusion` to gate an action (cross-chain bridge unlock, token mint, asset release, proof-of-payment) can be deceived into accepting a forged proof. The attacker proves "inclusion" of a non-existent Bitcoin transaction in a real, confirmed block. The corrupted value is the boolean proof result returned to the consumer — the direct analog to the MultiMerkleDistributor assigning rewards to a non-voter based on an unchecked Merkle leaf.

---

### Likelihood Explanation

Medium. The attacker needs only:
- Knowledge of any confirmed Bitcoin block's Merkle tree structure (fully public, derivable from block data).
- The ability to send a NEAR transaction (no privileged role required).

No private keys, social engineering, or off-chain infrastructure compromise is needed.

---

### Recommendation

1. **Remove the public exposure of v1**: Mark `verify_transaction_inclusion` as `#[private]` or delete it entirely, forcing all callers to use `verify_transaction_inclusion_v2`.
2. **If v1 must remain**: Add an explicit check that `merkle_proof.len()` equals the expected tree depth, derived from a separately validated coinbase proof — exactly what v2 does.
3. **Document consumer responsibility clearly**: Any contract consuming the `bool` result must use v2 exclusively; this should be enforced at the contract level, not left to convention.

---

### Proof of Concept

Consider a Bitcoin block with 4 transactions `[T0, T1, T2, T3]` whose Merkle tree is:

```
        root = hash(N01, N23)
       /                    \
N01 = hash(T0,T1)    N23 = hash(T2,T3)
     /      \               /      \
    T0       T1            T2       T3
```

**Attacker steps (no privileged access required):**

1. Obtain `N01 = double_sha256(T0 || T1)` from public block data.
2. Obtain `N23 = double_sha256(T2 || T3)` from public block data.
3. Call `verify_transaction_inclusion` with:
   - `tx_id = N01` ← an internal node, **not a real transaction**
   - `tx_block_blockhash` = the real block hash (present in mainchain)
   - `tx_index = 0`
   - `merkle_proof = [N23]`
   - `confirmations = 1`

4. Inside the contract, `compute_root_from_merkle_proof(N01, 0, [N23])` computes:
   - position 0 is even → `hash(N01, N23)` = `root` ✓

5. `root == header.block_header.merkle_root` → function returns **`true`**.

The contract has falsely certified that `N01` — a non-existent transaction — is included in the block. Any consumer acting on this result (e.g., releasing bridged funds) is exploited. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L287-289)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
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
