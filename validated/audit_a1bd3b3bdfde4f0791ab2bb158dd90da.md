### Title
Deprecated `verify_transaction_inclusion` Remains Callable On-Chain Without Coinbase Proof Check, Enabling Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The contract exposes two transaction-inclusion verification entry points. The current one, `verify_transaction_inclusion_v2`, guards against the 64-byte transaction Merkle-proof forgery attack by requiring a valid coinbase proof. The deprecated one, `verify_transaction_inclusion`, omits that guard entirely. Because Rust's `#[deprecated]` attribute is a compile-time warning only and imposes no on-chain restriction, any NEAR account can call the deprecated function directly and receive a `true` result for a forged proof.

---

### Finding Description

`verify_transaction_inclusion_v2` performs a mandatory coinbase-proof check before delegating to the deprecated function: [1](#0-0) 

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

`verify_transaction_inclusion` skips that check entirely and proceeds directly to the proof computation: [2](#0-1) 

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

The function carries a `#[deprecated]` attribute: [3](#0-2) 

but that attribute is a Rust compiler hint only. The function is still decorated with `#[pause]` and no role guard, meaning any unprivileged NEAR account can invoke it via RPC or from another NEAR contract. [4](#0-3) 

The coinbase-proof check exists precisely to close the well-known 64-byte transaction Merkle-proof forgery path (documented in the function's own deprecation notice and at https://www.bitmex.com/blog/64-Byte-Transactions). Without it, an attacker can supply a `tx_id` that is actually an internal Merkle-tree node, pair it with a crafted `merkle_proof`, and cause `compute_root_from_merkle_proof` to reproduce the block's real `merkle_root`, making the function return `true` for a transaction that was never mined. [5](#0-4) 

The only guard inside `verify_transaction_inclusion` that could stop this is: [6](#0-5) 

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

A non-empty forged proof passes this check trivially.

---

### Impact Explanation

Any NEAR bridge or application contract that calls `verify_transaction_inclusion` and acts on its boolean result (e.g., releasing wrapped BTC, crediting a user balance, or unlocking collateral) can be made to accept a proof for a Bitcoin transaction that was never confirmed. The corrupted value is the **proof result**: the function returns `true` where the correct answer is `false`. This is a direct analog to the corrupted `marketBalance` in H-01 — a state/result value that diverges from on-chain reality because a critical invariant check is absent on one specific entry point.

---

### Likelihood Explanation

Medium. The function is publicly reachable by any NEAR account with no role restriction. Any consuming contract that was deployed against the old API and has not migrated to `verify_transaction_inclusion_v2` is immediately exploitable. The 64-byte forgery technique is publicly documented and has known tooling.

---

### Recommendation

1. **Remove** `verify_transaction_inclusion` from the public ABI, or
2. **Redirect** it to `verify_transaction_inclusion_v2` internally (requiring the caller to supply coinbase proof data), or
3. **Add a hard runtime panic** (`env::panic_str("deprecated: use verify_transaction_inclusion_v2")`) so on-chain calls always revert.

---

### Proof of Concept

1. Identify any block hash `B` stored in the contract's `headers_pool` with a known `merkle_root R`.
2. Craft a 64-byte byte string `T` whose `double_sha256` equals an internal Merkle-tree node `N` that is a sibling of the real coinbase hash at some tree level.
3. Build a `merkle_proof` array `P` such that `compute_root_from_merkle_proof(T, idx, P) == R`.
4. Call `verify_transaction_inclusion` with `tx_id = T`, `tx_block_blockhash = B`, `tx_index = idx`, `merkle_proof = P`, `confirmations = 1`.
5. The function returns `true`.
6. A bridge contract that gates fund release on this result now releases funds for a Bitcoin transaction that was never mined.

`verify_transaction_inclusion_v2` would reject step 4 at the coinbase-proof `require!` before ever reaching the forged-proof computation. [7](#0-6)

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
