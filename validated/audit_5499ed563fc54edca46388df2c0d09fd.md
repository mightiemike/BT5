### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable Without Leaf-Node Validation, Enabling Merkle Proof Forgery - (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly callable entry point on the NEAR contract. It accepts a caller-supplied `tx_id` and `merkle_proof` but performs no validation that `tx_id` is a genuine leaf node (real transaction) rather than an internal Merkle tree node. An unprivileged caller can supply an internal-node hash as `tx_id` with a crafted shorter proof path and cause the function to return `true`, falsely confirming a transaction that does not exist in the block.

---

### Finding Description

`verify_transaction_inclusion` is marked `#[deprecated]` but remains a fully reachable `#[pause]`-gated public method. The function accepts caller-controlled `ProofArgs` (containing `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`) and validates them against the stored `ExtendedHeader` in `headers_pool`. [1](#0-0) 

The only structural check on the proof itself is: [2](#0-1) 

There is no validation that:
- `merkle_proof.len()` matches the expected tree depth (i.e., no coinbase proof cross-check)
- `tx_id` is a leaf node rather than an internal Merkle tree node
- `tx_index` is within the valid range implied by the proof length

The code itself documents this gap explicitly:

> *"This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."* [3](#0-2) 

`verify_transaction_inclusion_v2` was introduced to fix this by requiring a coinbase proof of equal length, which anchors the tree depth and forces `tx_id` to be a genuine leaf: [4](#0-3) 

But v1 was never removed and remains callable.

The Merkle computation in `compute_root_from_merkle_proof` uses only `current_position % 2` to decide left/right placement at each level, with no bounds or depth enforcement: [5](#0-4) 

---

### Impact Explanation

Any NEAR smart contract or off-chain consumer that calls `verify_transaction_inclusion` to gate a financial action (e.g., cross-chain bridge release, payment confirmation, collateral unlock) can be deceived into accepting a forged SPV proof. The attacker causes the contract to return `true` for a transaction that was never broadcast or confirmed on Bitcoin. This is a direct analog to the external report's unauthorized claim: stored parameters (the block's merkle root) exist, but the caller-supplied proof is not validated against the structural constraints those parameters imply (tree depth, leaf-node identity).

---

### Likelihood Explanation

The attack is well-documented (the 64-byte transaction Merkle forgery, https://www.bitmex.com/blog/64-Byte-Transactions). The entry point requires no special role — any NEAR account can call `verify_transaction_inclusion` directly. The attacker only needs a real Bitcoin block (publicly available) to extract an internal node hash and construct a valid-looking shorter proof. The function is not behind any access-control role check beyond the `#[pause]` gate, which is the default unpaused state.

---

### Recommendation

Remove `verify_transaction_inclusion` entirely from the contract, or add a hard `env::panic_str` body that unconditionally aborts, so it can no longer be called. All callers must migrate to `verify_transaction_inclusion_v2`, which enforces coinbase proof length equality and thereby anchors the tree depth, preventing internal-node forgery. [6](#0-5) 

---

### Proof of Concept

1. Select any Bitcoin mainchain block stored in the contract with ≥2 transactions (e.g., block at height `H` with merkle root `R`).
2. Obtain the two child hashes `L` and `R_child` at the root's immediate children. Their concatenation `L || R_child` (64 bytes) is the pre-image of `R`.
3. Craft `ProofArgs`:
   - `tx_id` = `L` (an internal node, not a real transaction)
   - `tx_block_blockhash` = hash of block `H`
   - `tx_index` = `0`
   - `merkle_proof` = `[R_child]` (one element, not the full depth)
   - `confirmations` = `1`
4. Call `verify_transaction_inclusion(args)`.
5. `compute_root_from_merkle_proof(L, 0, [R_child])` computes `double_sha256(L || R_child)` = `R`, which equals `header.block_header.merkle_root`.
6. The function returns `true` — falsely confirming that `L` (a non-existent transaction) is included in block `H`. [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

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

**File:** merkle-tools/src/lib.rs (L54-60)
```rust
fn compute_hash(first_tx_hash: &H256, second_tx_hash: &H256) -> H256 {
    let mut concat_inputs = Vec::with_capacity(64);
    concat_inputs.extend(first_tx_hash.0);
    concat_inputs.extend(second_tx_hash.0);

    double_sha256(&concat_inputs)
}
```
