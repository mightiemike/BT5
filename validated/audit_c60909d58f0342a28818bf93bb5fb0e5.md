### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable Without Coinbase Merkle Proof Validation, Enabling 64-Byte Transaction Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The contract exposes two transaction inclusion verification endpoints. The newer `verify_transaction_inclusion_v2` adds a coinbase Merkle proof check specifically to prevent the 64-byte transaction Merkle proof forgery attack. However, the original `verify_transaction_inclusion` (v1) remains a live, unpermissioned public method callable by any NEAR account. Rust's `#[deprecated]` attribute is a compiler hint only — it imposes no runtime restriction. Any caller that invokes v1 directly bypasses the coinbase proof validation entirely, allowing a forged `tx_id` (an internal Merkle tree node hash) to be accepted as a valid transaction inclusion proof.

This is a direct structural analog to the BabyJubjub bug: a validation routine exists and is correct, but it is not enforced in all reachable code paths that handle the same class of input.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to close the 64-byte transaction Merkle proof forgery vulnerability. It does so by first verifying a coinbase Merkle proof against the block's `merkle_root`, then delegating to v1: [1](#0-0) 

The coinbase check is the guard — it anchors the Merkle tree to a known-valid leaf (the coinbase transaction at index 0), making it impossible to substitute an internal node as a transaction hash.

The v1 function, however, performs no such check. It accepts a caller-supplied `tx_id` and `merkle_proof`, computes a root, and compares it directly to the stored `merkle_root`: [2](#0-1) 

The function is annotated `#[deprecated]` and carries an explicit warning that it "may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." Despite this, it remains a fully reachable public method with only a `#[pause]` gate (no role restriction): [3](#0-2) 

`ProofArgs` — the argument type for v1 — contains no `coinbase_tx_id` or `coinbase_merkle_proof` fields, so the validation cannot be performed even in principle through this path: [4](#0-3) 

The `compute_root_from_merkle_proof` function in `merkle-tools` is a pure hash-chain computation with no structural constraints on what `transaction_hash` represents: [5](#0-4) 

---

### Impact Explanation

A recipient NEAR contract that calls `verify_transaction_inclusion` (v1) to gate a financial action (e.g., releasing funds upon BTC transaction confirmation) can be deceived. An attacker constructs a 64-byte value that is a valid internal Merkle tree node of a real Bitcoin block, presents it as a `tx_id`, and supplies a Merkle proof path from that node to the root. The function returns `true`, falsely asserting transaction inclusion. The corrupted proof result is the return value of a public contract method — a boolean that downstream contracts treat as authoritative.

---

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion` directly. The 64-byte forgery technique is publicly documented (referenced in the contract's own comments: `https://www.bitmex.com/blog/64-Byte-Transactions`). A recipient contract that was integrated before v2 was introduced, or one that calls v1 by name, is immediately exploitable without any key material or social engineering.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with a role that prevents unprivileged callers from invoking it. The `#[deprecated]` annotation provides no runtime protection. The coinbase Merkle proof validation present in `verify_transaction_inclusion_v2` must be the only reachable code path for transaction inclusion proofs. [6](#0-5) 

---

### Proof of Concept

1. Identify a real Bitcoin block whose Merkle tree has at least two transactions. Let the internal node at depth 1 (hash of `tx0 || tx1`) be `N`.
2. Call `verify_transaction_inclusion` on the NEAR contract with:
   - `tx_id = N` (the internal node hash, 32 bytes — not a real transaction)
   - `tx_block_blockhash` = the hash of that block (already stored in the contract)
   - `tx_index = 0`
   - `merkle_proof` = the Merkle path from `N` up to the root (one element shorter than a real leaf proof)
   - `confirmations = 1`
3. `compute_root_from_merkle_proof(N, 0, &proof)` produces the correct `merkle_root` of the block.
4. The function returns `true`, falsely confirming that `N` is an included transaction.
5. Any recipient contract gating on this result accepts the forged proof. [7](#0-6)

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

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
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
