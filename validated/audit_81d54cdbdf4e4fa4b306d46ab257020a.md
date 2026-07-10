### Title
Proof-Verification Forgery via 64-Byte Transaction Merkle Proof Attack — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function remains publicly callable by any unprivileged NEAR caller and does not validate that the supplied `tx_id` is a leaf node (actual transaction) rather than an internal Merkle tree node. An attacker can supply a 64-byte internal node hash as `tx_id` with a correctly-sized proof path, causing the function to return `true` for a transaction that does not exist in the block.

---

### Finding Description

`verify_transaction_inclusion` (lines 288–323 of `contract/src/lib.rs`) delegates proof verification entirely to `merkle_tools::compute_root_from_merkle_proof`, comparing the computed root against the stored `header.block_header.merkle_root`: [1](#0-0) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` is a pure positional hash-chain computation: [2](#0-1) 

Neither function validates that `tx_id` is a leaf node. In Bitcoin's Merkle tree, every internal node at depth D is itself the SHA256d of two 32-byte child hashes (a 64-byte preimage). An attacker who knows the tree structure of any confirmed block can:

1. Select an internal node `H_internal` at depth D (e.g., `SHA256d(tx0 ‖ tx1)`).
2. Construct a proof of length D from that node to the root (one sibling hash per level).
3. Call `verify_transaction_inclusion` with `tx_id = H_internal`, `tx_index` matching the node's position at depth D, and the D-length proof.
4. `compute_root_from_merkle_proof` correctly walks D levels and produces the real Merkle root.
5. The equality check passes; the function returns `true`.

The only guard present is: [3](#0-2) 

This rejects an empty proof but does not prevent a proof that terminates at an internal node.

`verify_transaction_inclusion_v2` closes this gap by requiring a coinbase proof of the same length as the target proof, enforcing that both proofs span the full tree height: [4](#0-3) 

However, `verify_transaction_inclusion` lacks this constraint and remains a live, unguarded public entry point. The `#[deprecated]` Rust attribute is compile-time only; it does not prevent runtime invocation. The function carries only `#[pause]`, which is inactive by default: [5](#0-4) 

---

### Impact Explanation

Any NEAR smart contract that calls `verify_transaction_inclusion` to gate an action (e.g., releasing bridged funds, minting tokens, updating cross-chain state) can be made to authorize that action for a Bitcoin transaction that was never broadcast or confirmed. The corrupted proof result is a `true` return value from a public verification API, directly corrupting the authorization assumption of every downstream consumer of this function.

---

### Likelihood Explanation

**Medium.** The attack requires no privileged role, no leaked key, and no social engineering. All inputs needed — the block hash, the Merkle tree structure, and the internal node hashes — are publicly available from any Bitcoin block explorer. The attacker only needs to identify a block already accepted by the light client (on `mainchain_header_to_height`) and compute the internal node at the desired depth. The function is callable by any NEAR account.

---

### Recommendation

Remove `verify_transaction_inclusion` from the contract entirely, or add a hard runtime guard that unconditionally panics (e.g., `env::panic_str("use verify_transaction_inclusion_v2")`). If backward compatibility must be preserved, port the coinbase-proof length-equality check from `verify_transaction_inclusion_v2` into `verify_transaction_inclusion` before the Merkle root comparison.

---

### Proof of Concept

Consider a confirmed Bitcoin block `B` (already in the light client's main chain) with four transactions: `tx0, tx1, tx2, tx3`.

```
Merkle tree:
  root = SHA256d(H01 ‖ H23)
  H01  = SHA256d(tx0 ‖ tx1)      ← internal node at depth 1, position 0
  H23  = SHA256d(tx2 ‖ tx3)
```

**Attack call:**
```json
{
  "tx_id":              "<H01 as hex>",
  "tx_block_blockhash": "<B.hash>",
  "tx_index":           0,
  "merkle_proof":       ["<H23 as hex>"],
  "confirmations":      1
}
```

**Execution trace inside `verify_transaction_inclusion`:**
1. Block `B` is found in `mainchain_header_to_height` — passes.
2. Confirmation count check — passes (1 confirmation).
3. `compute_root_from_merkle_proof(H01, 0, [H23])`:
   - position 0 is even → compute `SHA256d(H01 ‖ H23)` = `root`
4. `root == header.block_header.merkle_root` → **`true`**.

`H01` is not a transaction; it is an internal node. No such transaction exists on the Bitcoin blockchain, yet the contract certifies its inclusion.

### Citations

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

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

**File:** contract/src/lib.rs (L348-365)
```rust
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
