### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable On-Chain, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function is still a live, publicly callable NEAR contract method. Any unprivileged NEAR account can invoke it directly, bypassing the coinbase-proof mitigation that was introduced in `verify_transaction_inclusion_v2`. This allows an attacker to forge a transaction-inclusion proof using an internal Merkle tree node, causing the function to return `true` for a transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to close the well-known 64-byte transaction Merkle-proof forgery vulnerability (https://www.bitmex.com/blog/64-Byte-Transactions). The mitigation works by requiring a coinbase proof of the **same length** as the transaction proof, which forces the claimed tree depth to be consistent with the actual leaf depth of the coinbase transaction. [1](#0-0) 

The deprecated predecessor, however, was never removed or restricted at the runtime level: [2](#0-1) 

Rust's `#[deprecated]` attribute emits a **compile-time warning only**. It does not prevent the function from being called via a NEAR transaction. Because the function is declared `pub` and carries no `#[private]`, `#[trusted_relayer]`, or `#[pause]`-with-role guard that would block an arbitrary caller, any NEAR account can invoke it directly.

The forgery exploits the fact that `compute_root_from_merkle_proof` is purely positional — it does not know whether the supplied hash is a leaf or an internal node: [3](#0-2) 

**Attack mechanics:**

Given a mainchain block whose Merkle tree has full depth D and at least two transactions, pick any internal node `H_parent` at depth `d` (where `d < D`). By construction, `H_parent = SHA256d(H_left ‖ H_right)`. The path from `H_parent` to the root is a valid Merkle proof of length `d`.

Call `verify_transaction_inclusion` with:
- `tx_id = H_parent`
- `tx_block_blockhash = <any mainchain block hash>`
- `tx_index = <position of H_parent in the level-d row>`
- `merkle_proof = <path from H_parent to root, length d>`
- `confirmations = 1`

`compute_root_from_merkle_proof(H_parent, position, proof)` produces the correct Merkle root, so the equality check passes and the function returns `true`. [4](#0-3) 

`verify_transaction_inclusion_v2` blocks this because it requires `coinbase_merkle_proof.len() == merkle_proof.len()`, and the coinbase proof for a real leaf at depth D has length D ≠ d. The deprecated function has no such guard. [5](#0-4) 

---

### Impact Explanation

Any dApp or cross-chain bridge that calls `verify_transaction_inclusion` (the deprecated endpoint) to gate a financial action — e.g., releasing wrapped tokens, settling a payment, or confirming a cross-chain swap — will accept a fraudulent proof. An attacker can claim that an arbitrary 32-byte value (an internal Merkle node) is a confirmed Bitcoin transaction in any mainchain block, enabling double-spend attacks, fraudulent asset minting, or unauthorized fund releases on the consuming contract.

The impact is a **complete bypass of the transaction-inclusion security guarantee** for any consumer of the deprecated API, which is the direct analog of the external report's "complete compromise of the network's security model."

---

### Likelihood Explanation

The attack requires no special privileges, no stake, no key material, and no coordination. Any NEAR account can call the deprecated function directly. The required inputs (block Merkle tree structure) are fully public on the Bitcoin blockchain. The only prerequisite is that the target block has at least two transactions, which is true of virtually every Bitcoin block.

---

### Recommendation

Remove `verify_transaction_inclusion` from the contract entirely, or convert it to always panic with a message directing callers to `verify_transaction_inclusion_v2`. A Rust `#[deprecated]` attribute is not a security boundary for an on-chain function.

```rust
// Replace the body with:
pub fn verify_transaction_inclusion(&self, _args: ProofArgs) -> bool {
    env::panic_str("verify_transaction_inclusion is removed; use verify_transaction_inclusion_v2");
}
```

---

### Proof of Concept

1. Choose any mainchain block `B` with ≥ 2 transactions. Let its Merkle tree have full depth D.
2. From the public Bitcoin blockchain, read the transaction list and compute the Merkle tree. Pick any internal node `H_parent` at depth `d = D - 1` (the penultimate level). Its two children are `H_left` and `H_right` (both real transaction hashes). The Merkle proof for `H_parent` is a single hash (the sibling of `H_parent` at depth `d`), giving a proof of length 1.
3. Call `verify_transaction_inclusion` on the NEAR contract:
   ```json
   {
     "tx_id": "<H_parent as hex>",
     "tx_block_blockhash": "<block B hash>",
     "tx_index": "<index of H_parent in the level-(D-1) row>",
     "merkle_proof": ["<sibling of H_parent>"],
     "confirmations": 1
   }
   ```
4. `compute_root_from_merkle_proof(H_parent, index, [sibling])` computes `SHA256d(H_parent ‖ sibling)` or `SHA256d(sibling ‖ H_parent)` depending on parity, which equals the actual Merkle root of block B.
5. The function returns `true`, falsely asserting that `H_parent` is a confirmed transaction in block B.

`verify_transaction_inclusion_v2` would reject this call because the forged `merkle_proof` has length 1 while the coinbase proof for a real leaf at depth D has length D ≥ 2, failing the length-equality check at line 349. [2](#0-1) [3](#0-2)

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
