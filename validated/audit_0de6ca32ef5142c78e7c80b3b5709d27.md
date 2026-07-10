### Title
Truncated Merkle Proof Forgery via Internal-Node Substitution Bypasses Coinbase-Length Mitigation — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` attempts to defeat the 64-byte Merkle-proof forgery attack by requiring the coinbase proof and the target-transaction proof to share the same length. However, the function never verifies that `coinbase_tx_id` is the *actual* coinbase transaction of the block. An unprivileged NEAR caller can substitute an internal Merkle-tree node for `coinbase_tx_id`, produce a matching-length truncated proof for both fields, and cause the contract to return `true` for a `tx_id` that is not a real leaf transaction — an exact structural analog of the MerkleDB exclusion-proof forgery.

---

### Finding Description

`verify_transaction_inclusion_v2` performs three checks:

1. **Length equality** — `merkle_proof.len() == coinbase_merkle_proof.len()`
2. **Coinbase root check** — `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root`
3. **Target-tx root check** — (delegated to `verify_transaction_inclusion`) `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == merkle_root` [1](#0-0) 

The design intent is: a valid coinbase proof of depth *L* proves the tree has *L* levels, so a tx proof of the same depth must also start from a leaf. The flaw is that check (2) does not bind `coinbase_tx_id` to the real coinbase transaction — it only verifies that *some* value, combined with *some* sibling hashes, hashes up to the root.

`compute_root_from_merkle_proof` is a pure arithmetic function that starts from whatever hash it is given and applies sibling hashes upward: [2](#0-1) 

It has no concept of "leaf vs. internal node." Any internal node at tree level *k* and position *p* can serve as a valid starting point for a proof of length *(L − k)* that correctly reaches the root.

**Concrete attack path** (tree of depth *L*, attacker uses level *k*):

| Step | Action |
|---|---|
| 1 | Obtain any real Bitcoin block accepted by the contract (public data). |
| 2 | Compute internal node **M** at level *k*, position 0 (left subtree root at that level). |
| 3 | Compute the *(L − k)*-element sibling path from **M** to the Merkle root. |
| 4 | Submit `coinbase_tx_id = M`, `coinbase_merkle_proof` = that path → check (2) passes. |
| 5 | Compute internal node **N** at level *k*, position *p* (any node at the same level). |
| 6 | Compute the *(L − k)*-element sibling path from **N** to the Merkle root. |
| 7 | Submit `tx_id = N`, `tx_index = p`, `merkle_proof` = that path. |
| 8 | Length check: both proofs have length *(L − k)* → passes. |
| 9 | Root check for **N**: `compute_root_from_merkle_proof(N, p, proof)` = root → passes. |
| 10 | Contract returns `true` for `tx_id = N`, which is not a real transaction. |

The `ProofArgsV2` struct accepts all of these fields from the caller without restriction: [3](#0-2) 

The only remaining guard in the delegated call is `!args.merkle_proof.is_empty()`, which is satisfied whenever *k < L*: [4](#0-3) 

---

### Impact Explanation

Any downstream NEAR contract or off-chain consumer that calls `verify_transaction_inclusion_v2` and acts on a `true` result — e.g., releasing bridged funds, minting tokens, or recording a settlement — can be deceived into accepting a fabricated Bitcoin transaction proof. The attacker proves that an internal Merkle-tree node (a hash of real transactions, not itself a transaction) is "included" in a block. In the 64-byte-transaction attack scenario, a 64-byte payload whose double-SHA256 equals that internal node would be accepted as a confirmed Bitcoin transaction, enabling theft of bridged assets or double-spend of a cross-chain payment.

---

### Likelihood Explanation

The attack requires no privileged role, no private key, and no mining capability. All inputs needed — the block's Merkle tree structure and internal node values — are derived from publicly available Bitcoin block data that the relayer already submits to the contract. Any NEAR account can call `verify_transaction_inclusion_v2` directly. The only additional requirement is constructing a 64-byte Bitcoin transaction whose hash matches a chosen internal node, which is the known (non-trivial but documented) 64-byte attack; without that step, the attacker can still prove arbitrary internal nodes are "transactions," which is sufficient to deceive any consumer that does not independently validate the transaction format.

---

### Recommendation

Bind `coinbase_tx_id` to the actual coinbase transaction of the block. Concretely:

- Require the caller to supply the full coinbase transaction bytes and verify `double_sha256(coinbase_tx_bytes) == coinbase_tx_id` on-chain, **and** verify that the coinbase transaction is structurally valid (e.g., first input is a coinbase input). This prevents substituting an internal node.
- Additionally, enforce that the proof length equals `ceil(log2(tx_count))` by requiring the caller to supply `tx_count` and checking it against the block header or a committed value, so truncated proofs are rejected regardless of the coinbase check.

The deprecated `verify_transaction_inclusion` (v1) retains the original 64-byte vulnerability with no mitigation at all and should be removed from the public API surface entirely. [5](#0-4) 

---

### Proof of Concept

Given a real Bitcoin block with Merkle root `R` and 4 transactions `[T0, T1, T2, T3]`:

```
Level 2 (root): R  = H(H01, H23)
Level 1:       H01 = H(T0, T1),   H23 = H(T2, T3)
Level 0 (leaf): T0, T1, T2, T3
```

Attacker chooses *k = 1*. Forged `ProofArgsV2`:

```
coinbase_tx_id        = H01          // internal node at level 1, position 0
coinbase_merkle_proof = [H23]        // length 1; H(H01, H23) = R ✓

tx_id                 = H23          // internal node at level 1, position 1
tx_index              = 1
merkle_proof          = [H01]        // length 1; H(H01, H23) = R ✓

confirmations         = 1
tx_block_blockhash    = <any mainchain block hash>
```

Checks performed by the contract:

| Check | Result |
|---|---|
| `merkle_proof.len() == coinbase_merkle_proof.len()` (1 == 1) | ✓ pass |
| `compute_root_from_merkle_proof(H01, 0, [H23]) == R` | ✓ pass |
| `compute_root_from_merkle_proof(H23, 1, [H01]) == R` | ✓ pass |

**Return value: `true`** — for `tx_id = H23`, which is not a real transaction.

### Citations

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

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

**File:** btc-types/src/contract_args.rs (L27-36)
```rust
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
