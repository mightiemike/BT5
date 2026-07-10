### Title
`verify_transaction_inclusion_v2` Accepts Internal Merkle Node as `coinbase_tx_id`, Nullifying 64-Byte Forgery Protection — (`contract/src/lib.rs`, `merkle-tools/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` is the contract's sole defense against the 64-byte transaction Merkle proof forgery attack. Its protection relies on the assumption that `coinbase_tx_id` is the actual coinbase leaf at depth 0. The function never enforces this: it only checks that `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root`. An unprivileged caller can supply an internal node hash as `coinbase_tx_id` with a shortened proof, pass every guard, and cause the function to return `true` for a transaction that does not exist in the block.

---

### Finding Description

`verify_transaction_inclusion_v2` performs two checks before returning a result:

1. **Length parity**: `merkle_proof.len() == coinbase_merkle_proof.len()` [1](#0-0) 

2. **Coinbase root check**: `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root` [2](#0-1) 

`compute_root_from_merkle_proof` is a pure hash-chain computation with no leaf-vs-internal-node distinction: [3](#0-2) 

Neither function checks that the supplied `coinbase_tx_id` is actually a leaf (i.e., a real transaction hash at depth 0 of the tree). Any value that, when combined with a valid sibling chain, produces `merkle_root` will pass.

**Concrete attack on a 4-transaction block** `[T0 (coinbase), T1, T2, T3]`:

```
Level 0 (leaves):   T0,  T1,  T2,  T3
Level 1 (internal): N01=H(T0,T1),  N23=H(T2,T3)
Level 2 (root):     merkle_root = H(N01, N23)
```

The attacker constructs a 64-byte fake transaction `F` whose serialized body is `concat(T0_bytes, T1_bytes)`. Bitcoin's double-SHA256 txid of `F` is therefore `H(T0, T1) = N01` — the level-1 internal node.

The attacker calls `verify_transaction_inclusion_v2` with:

| Field | Value |
|---|---|
| `coinbase_tx_id` | `N01` (internal node, not the real coinbase leaf) |
| `coinbase_merkle_proof` | `[N23]` (length 1) |
| `tx_id` | `N01` (txid of fake transaction F) |
| `tx_index` | `0` |
| `merkle_proof` | `[N23]` (length 1) |

**Guard-by-guard trace:**

- Length parity: `1 == 1` ✓
- Coinbase root: `compute_root_from_merkle_proof(N01, 0, [N23])` → position 0 is even → `H(N01, N23) = merkle_root` ✓ [4](#0-3) 
- Tx root (inside `verify_transaction_inclusion`): identical computation → `merkle_root` ✓ [5](#0-4) 

All guards pass. The function returns `true` for fake transaction `F`, which was never broadcast or mined.

The depth-binding invariant — that the coinbase proof length equals the true tree depth — is broken because the attacker reduced the apparent depth from 2 to 1 by anchoring at an internal node instead of the real coinbase leaf.

---

### Impact Explanation

Any downstream NEAR contract (bridge, escrow, oracle) that calls `verify_transaction_inclusion_v2` and acts on a `true` result can be deceived into treating a fabricated 64-byte Bitcoin transaction as confirmed. The attacker needs no special NEAR role; `verify_transaction_inclusion_v2` is a public, unpermissioned view/call. [6](#0-5) 

---

### Likelihood Explanation

The exploit requires only:
1. Any Bitcoin block with ≥ 2 transactions (i.e., every real block).
2. Knowledge of two adjacent leaf hashes at any level of the tree (publicly available from any Bitcoin node).
3. A single NEAR contract call with crafted arguments.

No privileged role, no key compromise, no special chain state is needed.

---

### Recommendation

The coinbase anchor must be validated as a genuine leaf, not merely as a value that hashes to the root. The standard mitigation is to enforce that the `coinbase_merkle_proof` length equals `ceil(log2(tx_count))` — the true tree depth derived from the number of transactions in the block — and to pass `tx_count` as a trusted parameter (verified against the block header's committed transaction count if available, or supplied by the relayer and range-checked). Alternatively, require the caller to supply the full ordered list of level-0 leaf hashes so the contract can independently recompute the root and confirm `coinbase_tx_id == leaves[0]`.

---

### Proof of Concept

```rust
// Pseudocode unit test (no NEAR runtime needed)
use merkle_tools::{compute_root_from_merkle_proof, compute_hash};

let t0 = sha256d(b"coinbase_tx_bytes");
let t1 = sha256d(b"tx1_bytes");
let t2 = sha256d(b"tx2_bytes");
let t3 = sha256d(b"tx3_bytes");

let n01 = compute_hash(&t0, &t1);   // internal node, level 1, position 0
let n23 = compute_hash(&t2, &t3);   // internal node, level 1, position 1
let root = compute_hash(&n01, &n23); // merkle_root

// Attacker uses internal node N01 as both coinbase_tx_id and tx_id
// with a 1-element proof [N23] — one level shorter than the real tree depth.

let coinbase_check = compute_root_from_merkle_proof(n01.clone(), 0, &vec![n23.clone()]);
assert_eq!(coinbase_check, root); // passes — coinbase guard bypassed

let tx_check = compute_root_from_merkle_proof(n01.clone(), 0, &vec![n23.clone()]);
assert_eq!(tx_check, root); // passes — tx inclusion "proven" for fake tx F

// verify_transaction_inclusion_v2 returns true for a transaction
// that does not exist in the block.
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

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
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
