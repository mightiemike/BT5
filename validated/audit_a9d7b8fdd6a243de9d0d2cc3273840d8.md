### Title
Deprecated `verify_transaction_inclusion` Enables 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function remains a live, publicly callable NEAR endpoint. It lacks the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` introduced to close the 64-byte transaction Merkle proof forgery vulnerability. Any unprivileged NEAR caller can supply a crafted `tx_id` that is an internal Merkle tree node hash and receive a `true` return value, falsely asserting that a non-existent Bitcoin transaction is confirmed on-chain.

---

### Finding Description

`verify_transaction_inclusion` is exposed as a public method with only a `#[pause]` guard — no `#[private]` restriction and no role requirement. [1](#0-0) 

The function's sole proof check is:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

It does not verify that `args.tx_id` is a leaf-level transaction hash. Because Bitcoin's Merkle tree is constructed by hashing pairs of nodes, any internal node hash is also a valid input to `compute_root_from_merkle_proof`. An attacker who knows the Merkle tree of any confirmed block can select an internal node as `tx_id`, build a proof path from that node to the root, and the comparison will succeed.

`verify_transaction_inclusion_v2` was introduced specifically to close this gap by requiring a coinbase Merkle proof that anchors the tree at a known leaf: [3](#0-2) 

However, the v1 function was never removed or restricted — `#[deprecated]` in Rust is a compile-time lint, not a runtime gate. The function remains fully callable on-chain.

---

### Impact Explanation

Any bridge, token-minting contract, or settlement system that calls `verify_transaction_inclusion` to authorize a payout can be deceived. An attacker crafts a proof for a Bitcoin transaction that never existed, the function returns `true`, and the downstream contract releases funds or mints tokens. The corrupted value is the **proof result** — a `true` return for a non-existent transaction — which directly breaks the authorization assumption of every consumer of this API.

---

### Likelihood Explanation

High. The function is callable by any unprivileged NEAR account when the contract is not paused. The 64-byte Merkle proof forgery technique is publicly documented (https://www.bitmex.com/blog/64-Byte-Transactions), requires no special hardware or privileged access, and needs only knowledge of a real Bitcoin block's Merkle tree structure, which is freely available from any Bitcoin node or block explorer.

---

### Recommendation

Remove `verify_transaction_inclusion` from the contract entirely, or add `#[private]` to make it uncallable externally, or apply the same coinbase Merkle proof validation that `verify_transaction_inclusion_v2` performs before delegating to the v1 logic.

---

### Proof of Concept

1. Attacker selects any confirmed Bitcoin block whose Merkle tree is known (e.g., from a block explorer).
2. Attacker picks an internal node hash `N` at depth `d` in the Merkle tree.
3. Attacker constructs a `merkle_proof` of length `d` that correctly leads from `N` to the block's `merkle_root`, using sibling hashes from the known tree.
4. Attacker calls `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real transaction)
   - `tx_block_blockhash` = the confirmed block's hash (already in `headers_pool`)
   - `tx_index` = the index corresponding to `N`'s position
   - `merkle_proof` = the constructed sibling path
   - `confirmations` = any value ≤ `gc_threshold`
5. `compute_root_from_merkle_proof(N, index, proof)` reproduces `merkle_root` exactly.
6. The function returns `true`.
7. Any downstream contract that gates an action on this return value (e.g., releasing bridged BTC) executes the attacker's intended action for a transaction that never occurred on Bitcoin.

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
