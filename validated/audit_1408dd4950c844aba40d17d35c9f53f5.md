### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase Merkle Proof Check Enforced by `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly callable NEAR function despite being deprecated. It lacks the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` was specifically introduced to enforce. Any NEAR caller — including downstream SPV-consumer contracts — can invoke v1 directly and receive a `true` result for a forged 64-byte internal-node "transaction," bypassing the only protection against that class of forgery.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to close the 64-byte transaction Merkle proof forgery vulnerability (the "CVE-2017-12842 / BitMEX" attack). It does so by requiring a separate coinbase Merkle proof that anchors the tree root independently:

```rust
// contract/src/lib.rs  ~L358-L365
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
```

After that check passes, v2 delegates to v1:

```rust
// contract/src/lib.rs  ~L368
self.verify_transaction_inclusion(args.into())
```

v1 itself contains **no coinbase check**. Its only Merkle-level guard is:

```rust
// contract/src/lib.rs  ~L315-L322
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

The `#[deprecated]` Rust attribute is a **compile-time hint only**. It has zero effect on the deployed WASM binary: the function remains a fully reachable public NEAR entry point. Any caller that supplies a `ProofArgs` struct directly to `verify_transaction_inclusion` receives the same `bool` result as if they had called v2, but without the coinbase anchor check.

---

### Impact Explanation

The 64-byte attack allows an adversary to craft a 64-byte blob that is simultaneously a valid serialized internal Merkle tree node and a plausible "transaction ID." By submitting this blob as `tx_id` with a carefully chosen `tx_index` and `merkle_proof`, the attacker can make `compute_root_from_merkle_proof` return the block's real `merkle_root` for a transaction that was never mined. `verify_transaction_inclusion` (v1) will return `true`.

Any downstream NEAR contract that calls v1 — either because it was written before v2 existed, or because it calls the function by name without checking the deprecation notice — will accept this forged proof as valid. This corrupts the SPV guarantee that is the contract's sole purpose: a non-existent Bitcoin transaction will be treated as confirmed.

---

### Likelihood Explanation

The entry point is unconditionally reachable by any unprivileged NEAR account or contract. No role, stake, or special permission is required. The `#[pause]` macro can gate it if the contract is paused, but in normal operation it is open. Downstream integrators who have not migrated to v2 — a realistic scenario given the function was only soft-deprecated — are silently exposed. The 64-byte forgery technique is publicly documented and has known tooling.

---

### Recommendation

Remove `verify_transaction_inclusion` as a public NEAR entry point entirely, or gate it with `#[private]` so it can only be called by the contract itself (as v2 already does internally). The internal call from v2 does not need it to be public. Alternatively, add the same coinbase proof requirement directly inside v1 so that both paths enforce the same invariant, making the bypass impossible regardless of which function a caller chooses.

---

### Proof of Concept

1. Identify a real Bitcoin block whose `merkle_root` is known and stored in the contract.
2. Construct a 64-byte blob `F` such that `SHA256d(SHA256d(F))` equals the block's `merkle_root` when treated as a single-element Merkle tree (or use a crafted multi-level proof path — the standard 64-byte attack).
3. Call `verify_transaction_inclusion` directly (not v2) with:
   - `tx_id = F`
   - `tx_block_blockhash` = the target block hash
   - `tx_index = 0`
   - `merkle_proof = []` (or a crafted path)
   - `confirmations = 1`
4. Because v1 skips the coinbase anchor check, `compute_root_from_merkle_proof` returns `merkle_root`, and the function returns `true` for a transaction that does not exist on-chain.

The root cause is the asymmetry between the two public entry points: [1](#0-0) [2](#0-1)

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
