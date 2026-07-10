### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Forgery Protection — (`File: contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is still a live `pub` NEAR entry point despite being deprecated. Any unprivileged caller can invoke it directly, completely bypassing the coinbase Merkle proof check that `verify_transaction_inclusion_v2` adds to defeat the 64-byte transaction Merkle proof forgery attack. This is the exact same bug class as the reference report: a weaker function (`close_perp` / `verify_transaction_inclusion`) remains reachable and skips a critical obligation check that the stronger replacement (`try_to_close_perp` / `verify_transaction_inclusion_v2`) enforces.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability (https://www.bitmex.com/blog/64-Byte-Transactions). It does so by requiring the caller to also supply a coinbase Merkle proof, which anchors the proof tree to index 0 and makes it impossible to substitute an internal Merkle node as a fake `tx_id`. [1](#0-0) 

The old function, `verify_transaction_inclusion`, carries only a Rust `#[deprecated]` attribute. In Rust, `#[deprecated]` is a lint-level compiler warning — it does **not** remove the function from the compiled WASM binary or prevent runtime dispatch. Because the function is `pub` and decorated with `#[pause]` (a NEAR SDK macro that registers it as a callable method), it remains a fully reachable contract entry point on-chain. [2](#0-1) 

The function's own doc comment acknowledges the broken invariant:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [3](#0-2) 

The verification logic inside `verify_transaction_inclusion` only checks:
1. Confirmation depth against `gc_threshold`
2. Block membership in `mainchain_header_to_height`
3. Non-empty `merkle_proof`
4. `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.merkle_root` [4](#0-3) 

Step 4 is the vulnerable step. `compute_root_from_merkle_proof` is a pure hash-chain computation: [5](#0-4) 

It accepts any 32-byte value as `transaction_hash`. If an attacker supplies a value that is actually an internal Merkle tree node (i.e., `SHA256d(left_child || right_child)` for some pair of real transactions), the computation will still produce the correct Merkle root, and the function returns `true` for a transaction that does not exist.

The `ProofArgs` struct that the caller fully controls: [6](#0-5) 

---

### Impact Explanation

Any recipient contract or off-chain system that calls `verify_transaction_inclusion` (instead of `verify_transaction_inclusion_v2`) to gate an action — e.g., releasing bridged assets, crediting a cross-chain payment, or unlocking collateral — can be deceived into accepting a forged proof. The attacker receives the benefit (assets, credit, access) without having made the corresponding Bitcoin transaction. The contract's canonical chain state (`mainchain_header_to_height`, `headers_pool`) is not corrupted, but the **proof result** returned to the consumer is false, which is the security-critical output of this contract.

---

### Likelihood Explanation

The entry point is unconditionally reachable by any NEAR account with no role, stake, or permission requirement. The 64-byte forgery technique is publicly documented and has known tooling. Any integrator that reads the contract ABI (which lists both methods) may call the deprecated method, either by mistake or deliberately. The likelihood is **high** for integrators who do not audit the deprecation notice, and **medium** for deliberate attackers who discover the bypass.

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or gate it behind a role that no external caller holds, so it is no longer a callable NEAR entry point. Alternatively, replace its body with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")`. The internal call from `verify_transaction_inclusion_v2` can be refactored to a private helper that both methods share, so the coinbase-proof gate cannot be bypassed.

---

### Proof of Concept

1. Identify a confirmed Bitcoin block `B` at height `H` already stored in the contract's mainchain (e.g., via `get_block_hash_by_height(H)`).
2. From the public Bitcoin blockchain, obtain the full transaction list for block `B` and compute any internal Merkle node `N = SHA256d(tx_left || tx_right)` at depth ≥ 1.
3. Construct a valid Merkle proof path from `N` up to the Merkle root (this is a standard Merkle proof for the subtree rooted at `N`).
4. Call `verify_transaction_inclusion` directly (bypassing `verify_transaction_inclusion_v2`) with:
   - `tx_id = N` (the internal node, not a real transaction hash)
   - `tx_block_blockhash = block_hash(B)`
   - `tx_index` = position of `N` in its level of the tree
   - `merkle_proof` = the proof path from `N` to the root
   - `confirmations = 1`
5. The function returns `true`. No such transaction `N` exists on Bitcoin; the proof is forged.
6. A recipient contract that calls `verify_transaction_inclusion` to authorize a cross-chain action now executes that action for a non-existent payment. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
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

**File:** contract/src/lib.rs (L347-368)
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
