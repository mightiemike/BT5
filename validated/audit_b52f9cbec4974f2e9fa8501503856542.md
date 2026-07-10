### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling Merkle Proof Forgery via Internal Node Substitution — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is still a live, publicly callable NEAR contract method despite being marked `#[deprecated]`. It lacks the coinbase-anchored Merkle proof check that `verify_transaction_inclusion_v2` introduced to mitigate the 64-byte transaction Merkle proof forgery vulnerability. Any unprivileged NEAR caller can invoke the deprecated function directly, bypassing the coinbase guard entirely, and obtain a `true` verification result for a `tx_id` that is an internal Merkle tree node rather than a real transaction.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery attack (https://www.bitmex.com/blog/64-Byte-Transactions). It does so by requiring the caller to also supply a coinbase proof anchored at index 0, which forces the proof to be rooted in a real leaf transaction. [1](#0-0) 

However, the original `verify_transaction_inclusion` was not removed — it remains a `pub` NEAR method decorated only with `#[deprecated]` and `#[pause]`. In Rust/NEAR, `#[deprecated]` is a compiler-level lint warning; it imposes no runtime restriction. Any NEAR account can call the method directly on-chain. [2](#0-1) 

The function's own docstring acknowledges the flaw explicitly:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [3](#0-2) 

The core verification logic in `compute_root_from_merkle_proof` is position-agnostic: it accepts any starting hash and any proof path, with no check that the starting hash is a leaf node. [4](#0-3) 

The only guard in `verify_transaction_inclusion` is `require!(!args.merkle_proof.is_empty(), ...)`, which does not prevent an attacker from supplying an internal node hash with a valid (shorter) proof path. [5](#0-4) 

---

### Impact Explanation

Any downstream NEAR contract or off-chain application that calls `verify_transaction_inclusion` to gate a privileged action (e.g., releasing bridged funds, minting tokens, updating cross-chain state) can be deceived into accepting a forged transaction inclusion proof. The function returns `true` for a `tx_id` that was never a real transaction in the block, corrupting the proof result that the entire SPV model depends on. [6](#0-5) 

---

### Likelihood Explanation

The entry path requires no privileges: any NEAR account can call `verify_transaction_inclusion` directly. The attacker only needs knowledge of a real mainchain block's Merkle tree structure (publicly available from any Bitcoin node) to compute a valid internal-node proof. The `#[deprecated]` attribute provides zero on-chain enforcement. [7](#0-6) 

---

### Recommendation

Remove `verify_transaction_inclusion` from the public contract ABI entirely, or gate it with an access-control role that prevents unprivileged callers from invoking it. The `#[deprecated]` attribute must not be relied upon as a security boundary on NEAR — it is a Rust compiler hint only. All callers should be migrated to `verify_transaction_inclusion_v2`. [7](#0-6) 

---

### Proof of Concept

Given a real mainchain block `B` with `merkle_root = R` and a Merkle tree of depth `d`:

1. Attacker fetches block `B`'s full transaction list from any Bitcoin node and reconstructs its Merkle tree.
2. Attacker selects any internal node `N` at depth `k` (where `0 < k < d`). `N` is the SHA256d hash of two child hashes — it is not a real transaction.
3. Attacker computes the proof path `P` of length `k` from `N` up to `R`.
4. Attacker calls on-chain:
   ```
   verify_transaction_inclusion({
     tx_id: N,
     tx_block_blockhash: B,
     tx_index: <position of N at depth k>,
     merkle_proof: P,   // non-empty, length k
     confirmations: 1
   })
   ```
5. `compute_root_from_merkle_proof(N, position, P)` traverses `k` steps and arrives at `R`.
6. `R == header.block_header.merkle_root` → function returns `true`.
7. Any consumer contract acting on this result treats the non-existent transaction `N` as confirmed. [4](#0-3) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L276-279)
```rust
    ///
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
