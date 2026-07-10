### Title
Coinbase Merkle Proof Check Does Not Enforce Leaf-Level Binding — Internal Node Accepted as `coinbase_tx_id`, Enabling Fake Transaction Inclusion Proof — (`contract/src/lib.rs`, `merkle-tools/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` is intended to defeat the 64-byte Merkle proof forgery attack by requiring a valid coinbase proof anchored at index 0. However, the check only verifies that `coinbase_tx_id` hashes up to `merkle_root` when placed at position 0 — it does **not** verify that `coinbase_tx_id` is a leaf-level transaction hash. An attacker can supply an internal Merkle tree node as `coinbase_tx_id`, satisfy the coinbase check, and then reuse the identical proof to "prove" that same internal node is a valid `tx_id`, causing the function to return `true` for a transaction that does not exist in the block.

---

### Finding Description

**Entrypoint:** `verify_transaction_inclusion_v2` is a public NEAR contract view call, reachable by any caller.

**Guard analysis — all guards traced:**

1. **Length check** (`lib.rs:348-351`): `merkle_proof.len() == coinbase_merkle_proof.len()` — satisfied trivially by using equal-length proofs. [1](#0-0) 

2. **Coinbase proof check** (`lib.rs:358-365`): verifies `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == header.block_header.merkle_root`. This check is **not** a leaf-binding check — it accepts any value that hashes to `merkle_root` at position 0, including an internal node. [2](#0-1) 

3. **Non-empty proof check** (`lib.rs:315`): `require!(!args.merkle_proof.is_empty(), ...)` — this blocks the question's specific 2-transaction/empty-proof scenario, but does **not** block the general attack with non-empty proofs. [3](#0-2) 

4. **Transaction inclusion check** (`lib.rs:318-322`): `compute_root_from_merkle_proof(tx_id, tx_index, &merkle_proof) == header.block_header.merkle_root` — no check that `tx_id` is a leaf node. [4](#0-3) 

**`compute_root_from_merkle_proof` is position-only, not depth-aware:** [5](#0-4) 

The function iterates over the proof array and hashes upward. It has no concept of tree depth or whether the starting hash is a leaf or an internal node. Any hash that produces `merkle_root` after applying the proof is accepted.

---

### Impact Explanation

An attacker can call `verify_transaction_inclusion_v2` with:
- `coinbase_tx_id` = an internal Merkle node (e.g., `h01`)
- `coinbase_merkle_proof` = the sibling subtree hash (e.g., `[h23]`)
- `tx_id` = the same internal node `h01`
- `tx_index` = 0
- `merkle_proof` = the same `[h23]`

Both the coinbase check and the transaction inclusion check compute the same path and both equal `merkle_root`. The function returns `true` for `tx_id = h01`, which is not a real transaction in the block. Any downstream system that relies solely on this contract's boolean return value to confirm a payment or release funds can be deceived.

---

### Likelihood Explanation

- No privileged role, DAO, or key compromise required.
- The attacker only needs a real block in the contract's `headers_pool` (any confirmed block) and knowledge of its Merkle tree structure (fully public from Bitcoin RPC).
- The attack is deterministic and requires no brute force or cryptographic weakness.
- The `coinbase_tx_id` field is fully attacker-controlled with no external validation.

---

### Recommendation

The coinbase proof must be bound to a **leaf node at depth equal to `merkle_proof.len()`**. Specifically:

1. **Enforce proof depth equality**: `coinbase_merkle_proof.len()` must equal `merkle_proof.len()` (already done), **and** the coinbase proof must traverse exactly `log2(num_txs)` levels — i.e., the proof length must match the actual tree depth. This requires knowing the number of transactions in the block, which should be stored or derivable.

2. **Alternatively, enforce `coinbase_tx_id != tx_id`**: This is a weaker mitigation but prevents the trivial reuse attack. It does not prevent all variants.

3. **Strongest fix**: Store the coinbase txid in the block header metadata at submission time and compare `args.coinbase_tx_id` against the stored value, eliminating attacker control over this field entirely.

---

### Proof of Concept

Given a real block with 4 transactions `[tx0, tx1, tx2, tx3]`:

```
h01  = dsha256(tx0 || tx1)
h23  = dsha256(tx2 || tx3)
root = dsha256(h01 || h23)   ← stored in block header
```

Attacker submits to `verify_transaction_inclusion_v2`:

```
coinbase_tx_id        = h01          // internal node, NOT a real txid
coinbase_merkle_proof = [h23]        // length 1
tx_id                 = h01          // same internal node
tx_index              = 0
merkle_proof          = [h23]        // same proof, length 1
confirmations         = 0
```

**Trace:**

1. Length check: `1 == 1` ✓
2. Coinbase check: `compute_root_from_merkle_proof(h01, 0, [h23])` → `dsha256(h01 || h23)` = `root` ✓
3. `verify_transaction_inclusion` called:
   - `merkle_proof.is_empty()` → false ✓
   - `compute_root_from_merkle_proof(h01, 0, [h23])` = `root` == `header.merkle_root` ✓
   - Returns `true`

**Result:** `verify_transaction_inclusion_v2` returns `true` for `tx_id = h01`, which is an internal Merkle node and not an actual transaction in the block. The invariant is violated.

**Note on the question's specific proof idea:** The 2-transaction/empty-proof variant (`coinbase_merkle_proof = []`, `merkle_proof = []`) is blocked by the `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard at `lib.rs:315`. The correct minimal attack requires a block with ≥4 transactions and proof length ≥1, as shown above. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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
