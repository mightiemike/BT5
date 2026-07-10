### Title
Incomplete Coinbase Validation in `verify_transaction_inclusion_v2` Allows Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` is the contract's recommended, non-deprecated SPV proof entry point. It is supposed to close the 64-byte transaction Merkle-proof forgery vulnerability by requiring a coinbase proof of equal length. However, the coinbase check only verifies that the supplied `coinbase_tx_id` hashes to the block's Merkle root via the supplied `coinbase_merkle_proof`; it never validates that `coinbase_tx_id` is the *actual* coinbase transaction of the block. An unprivileged NEAR caller can supply two internal Merkle-tree node hashes — one as `coinbase_tx_id`, one as `tx_id` — both of which satisfy every check, causing the function to return `true` for a transaction that does not exist.

---

### Finding Description

The relevant code path is:

```
verify_transaction_inclusion_v2  (contract/src/lib.rs:347-369)
  ├─ require merkle_proof.len() == coinbase_merkle_proof.len()   (line 349)
  ├─ require compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof)
  │          == header.block_header.merkle_root                  (lines 359-365)
  └─ self.verify_transaction_inclusion(args.into())              (line 368)
``` [1](#0-0) 

The coinbase guard at lines 359-365 only checks that the supplied `coinbase_tx_id`, treated as a leaf at position `0`, produces the stored Merkle root through the supplied `coinbase_merkle_proof`. It does **not** verify that `coinbase_tx_id` equals the hash of the block's actual first transaction.

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` is a pure positional computation:

```
for proof_hash in merkle_proof {
    if current_position % 2 == 0 { current_hash = H(current_hash, proof_hash) }
    else                          { current_hash = H(proof_hash, current_hash) }
    current_position /= 2;
}
``` [2](#0-1) 

Any value that occupies position `0` at some depth of the tree will satisfy the coinbase check when paired with the correct sibling at that depth.

**Concrete example — block with 4 transactions T0, T1, T2, T3:**

```
Level 0 (leaves): T0   T1   T2   T3
Level 1:          H01=H(T0,T1)  H23=H(T2,T3)
Level 2 (root):   R = H(H01, H23)
```

Attacker supplies:

| Field | Value |
|---|---|
| `coinbase_tx_id` | `H01` (internal node, position 0 at depth 1) |
| `coinbase_merkle_proof` | `[H23]` (length 1) |
| `tx_id` | `H23` (internal node, position 1 at depth 1) |
| `tx_index` | `1` |
| `merkle_proof` | `[H01]` (length 1) |
| `confirmations` | `1` |

Check 1 — length equality: `1 == 1` ✓  
Check 2 — coinbase root: `compute_root_from_merkle_proof(H01, 0, [H23])` = `H(H01, H23)` = `R` ✓  
Check 3 — tx root (inside deprecated inner call): `compute_root_from_merkle_proof(H23, 1, [H01])` = `H(H01, H23)` = `R` ✓  
Non-empty proof check: `[H01].len() > 0` ✓

Result: `verify_transaction_inclusion_v2` returns **`true`** for `tx_id = H23`, which is an internal Merkle-tree node, not a real transaction. [3](#0-2) 

The same pattern generalises to any block with ≥ 3 transactions (any tree depth ≥ 2).

---

### Impact Explanation

The function is the authoritative on-chain SPV oracle for the entire system. Consumer contracts (bridges, atomic-swap protocols, cross-chain lending) call it and act on its boolean result. A forged `true` return for a non-existent transaction allows an attacker to:

- Claim bridge payouts for Bitcoin transactions that were never broadcast or confirmed.
- Trigger any cross-chain action gated on transaction inclusion (token mints, collateral releases, etc.).
- Corrupt the proof-result invariant that the rest of the protocol depends on.

The corrupted canonical value is the **proof result** (`bool`) returned by `verify_transaction_inclusion_v2`.

---

### Likelihood Explanation

The attack requires no privileged role, no private key, and no mining power. The attacker only needs:

1. A block already accepted into the contract's main chain (public information).
2. Knowledge of that block's Merkle tree (derivable from any Bitcoin full node or block explorer).
3. The ability to call `verify_transaction_inclusion_v2` on NEAR — open to any account.

The attack is deterministic and requires a single NEAR transaction.

---

### Recommendation

The coinbase guard must verify that `coinbase_tx_id` is the *actual* coinbase transaction of the target block, not merely that it hashes to the root at position 0. Concretely:

1. **Store the coinbase txid** in `ExtendedHeader` / `LightHeader` at block-submission time (requires a data-model change but is the most robust fix).
2. **Alternatively**, require the caller to supply the raw coinbase transaction bytes, recompute its txid inside the contract, and use that computed value instead of the caller-supplied `coinbase_tx_id`.

Without one of these changes, the length-equality guard is by

### Citations

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

**File:** merkle-tools/src/lib.rs (L42-51)
```rust
    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
```
