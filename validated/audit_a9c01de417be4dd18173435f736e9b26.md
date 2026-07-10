### Title
Deprecated `verify_transaction_inclusion` Accepts Unvalidated `tx_id`, Enabling Merkle Proof Forgery via Internal Node Hash — (File: `contract/src/lib.rs`)

---

### Summary

The still-callable deprecated `verify_transaction_inclusion` function accepts any 32-byte `tx_id` without validating that it is a leaf-level transaction hash. An unprivileged NEAR caller can supply an internal Merkle node hash as `tx_id` with a correspondingly shorter proof, causing the function to return `true` for a transaction that does not exist in the block. This is the direct analog of the VDF precomputation issue: just as the VDF input `x` was not validated to be a large prime (allowing precomputed attacks), the `tx_id` input is not validated to be a real transaction hash, allowing Merkle proof forgery with precomputed internal node hashes.

---

### Finding Description

`verify_transaction_inclusion` computes the Merkle root from the caller-supplied `tx_id`, `tx_index`, and `merkle_proof`, then compares it to the stored block's `merkle_root`: [1](#0-0) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` simply walks the proof path without any constraint on what `tx_id` represents: [2](#0-1) 

No validation is performed to ensure `tx_id` is a leaf-level transaction hash rather than an internal Merkle node. The only guard on the proof is: [3](#0-2) 

For a block with transactions `[T1, T2, T3, T4]`, the Merkle tree has internal nodes `H12 = hash(H1, H2)` and `H34 = hash(H3, H4)`, with `Root = hash(H12, H34)`. An attacker can:

1. Use `H12` as `tx_id` with `tx_index = 0`
2. Provide `merkle_proof = [H34]`
3. `compute_root_from_merkle_proof(H12, 0, [H34])` = `hash(H12, H34)` = `Root` ✓

The function returns `true` for the non-existent "transaction" `H12`.

The `verify_transaction_inclusion_v2` function fixes this by requiring a coinbase Merkle proof: [4](#0-3) 

But the deprecated function remains callable. The `#[deprecated]` Rust attribute only warns Rust-language callers at compile time; NEAR protocol callers invoke it directly via the contract ABI with no warning.

The root cause is identical to the VDF precomputation analog: the contract accepts a cryptographic input (`tx_id`) without enforcing that it belongs to the valid domain (leaf-level transaction hashes), allowing an attacker to precompute a valid proof for a crafted input (an internal node hash) that satisfies the verification equation without representing a real transaction.

---

### Impact Explanation

Any downstream NEAR contract or user relying on `verify_transaction_inclusion` to confirm Bitcoin transaction inclusion can be deceived into accepting a forged proof. An attacker can claim that a Bitcoin payment was made when it was not, by supplying an internal Merkle node hash as `tx_id` with a valid but shorter proof path. This corrupts the **proof result** — the canonical security output of the light client — and could allow theft of funds from downstream contracts that gate withdrawals or settlements on this verification.

---

### Likelihood Explanation

Medium. The attack requires:
1. Knowledge of the 64-byte transaction / internal-node Merkle forgery technique (publicly documented).
2. Access to the Merkle tree structure of any real mainchain block (publicly available on-chain).
3. Calling the deprecated but still-live `verify_transaction_inclusion` entry point with crafted arguments.

No privileged role, key material, or social engineering is required. Any unprivileged NEAR account can execute this.

---

### Recommendation

Remove `verify_transaction_inclusion` entirely from the contract, or gate it so it panics unconditionally (forcing callers to migrate to `verify_transaction_inclusion_v2`). If removal is not immediately feasible, add the same coinbase-proof guard present in `verify_transaction_inclusion_v2` to the deprecated path.

---

### Proof of Concept

1. Identify any real mainchain block with ≥ 2 transactions (all real Bitcoin blocks qualify).
2. Compute `H12 = double_sha256(H1 || H2)` (the left internal node at depth 1).
3. Compute `H34 = double_sha256(H3 || H4)` (the right internal node at depth 1).
4. Call `verify_transaction_inclusion` with:
   - `tx_id = H12`
   - `tx_index = 0`
   - `merkle_proof = [H34]`
   - `tx_block_blockhash = <any real mainchain block hash>`
   - `confirmations = 1`
5. `compute_root_from_merkle_proof(H12, 0, [H34])` computes `hash(H12, H34) = Root`, which equals `header.block_header.merkle_root`.
6. The function returns `true` — confirming inclusion of a transaction that does not exist. [5](#0-4) [2](#0-1)

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

**File:** contract/src/lib.rs (L358-365)
```rust
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
