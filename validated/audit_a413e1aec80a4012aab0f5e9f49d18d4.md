### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the 64-Byte Merkle Forgery Protection Added in v2 - (File: contract/src/lib.rs)

### Summary

`verify_transaction_inclusion_v2` was introduced specifically to enforce a coinbase Merkle proof check that prevents the 64-byte transaction forgery attack. However, the original `verify_transaction_inclusion` (v1) remains fully callable by any unprivileged NEAR account. An attacker can call v1 directly to bypass the coinbase proof restriction and obtain a `true` proof-verification result for a fabricated transaction.

### Finding Description

`verify_transaction_inclusion_v2` enforces two invariants that v1 does not:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` — ensures the coinbase proof covers the same tree depth.
2. The coinbase transaction's Merkle proof must reconstruct the block's `merkle_root` — this is the guard against the 64-byte internal-node forgery. [1](#0-0) 

After both checks pass, v2 delegates to v1: [2](#0-1) 

v1 itself performs only the standard Merkle-root reconstruction against the claimed `tx_id`: [3](#0-2) 

The `#[deprecated]` Rust attribute on v1 is a **compiler hint only**; it does not restrict on-chain invocation. v1 remains a live, `#[pause]`-gated public method: [4](#0-3) 

The 64-byte forgery attack (documented in the v2 docstring, referencing the BitMEX research post) works as follows: a 64-byte SHA-256 preimage can be crafted so that its double-SHA-256 hash equals an internal Merkle-tree node. Because v1 only checks that `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.merkle_root`, a proof path that terminates at such an internal node passes validation even though `tx_id` is not a real leaf transaction. v2 closes this by requiring the coinbase proof to independently anchor the tree, but v1 has no such anchor.

### Impact Explanation

Any downstream NEAR contract or off-chain application that calls `verify_transaction_inclusion` (v1) to gate a privileged action (e.g., releasing bridged assets, minting tokens, or updating cross-chain state) can be deceived into accepting a fabricated Bitcoin transaction proof. The attacker supplies a crafted 64-byte `tx_id` and a valid internal-node Merkle path; v1 returns `true`; the downstream action executes without a real on-chain Bitcoin transaction.

### Likelihood Explanation

The entry path is fully unprivileged: any NEAR account can call `verify_transaction_inclusion` with arbitrary `ProofArgs`. The 64-byte forgery technique is publicly documented and has known tooling. The only prerequisite is that the target block is already in the contract's `headers_pool` (trivially satisfied for any mainchain block). Likelihood is **medium-high** for any integration that has not independently audited which version of the API it calls.

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with an access-control role so it cannot be called by arbitrary accounts. If backward compatibility must be preserved, add the same coinbase Merkle proof check directly inside v1, making the two functions equivalent in security posture. The deprecation marker alone is insufficient because it carries no runtime enforcement.

### Proof of Concept

1. Identify any block `B` in the contract's mainchain whose `merkle_root` is known.
2. Construct a 64-byte value `fake_tx` whose double-SHA-256 equals an internal node `N` in `B`'s Merkle tree.
3. Build a valid Merkle path from `N` up to `merkle_root`.
4. Call (NEAR RPC):
   ```
   verify_transaction_inclusion({
     tx_id: fake_tx,
     tx_block_blockhash: B.hash,
     tx_index: <index of N>,
     merkle_proof: <path from N to merkle_root>,
     confirmations: 1
   })
   ```
5. The function returns `true` because `compute_root_from_merkle_proof(fake_tx, index, path) == merkle_root` holds for the internal-node path, with no coinbase anchor to detect the forgery. [3](#0-2)

### Citations

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L316-323)
```rust

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
