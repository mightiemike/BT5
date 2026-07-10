### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase-Proof Prerequisite Added in v2 — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, unrestricted public entry point on the NEAR contract. Any unprivileged caller can invoke it directly, skipping the coinbase Merkle proof check that `verify_transaction_inclusion_v2` was introduced to enforce. This re-opens the 64-byte transaction Merkle proof forgery vulnerability that v2 was designed to close.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to mitigate the well-known 64-byte transaction Merkle proof forgery attack (documented at the BitMEX link in the code comments). The fix works by requiring the caller to also supply a coinbase Merkle proof, which anchors the tree structure and prevents an internal node from being mistaken for a leaf transaction. [1](#0-0) 

However, the original v1 function was only annotated with `#[deprecated]` — a Rust compiler hint that produces a warning but **does not restrict access**. The function remains `pub` and is a fully reachable NEAR contract method: [2](#0-1) 

The v1 function's only guard against an empty proof is: [3](#0-2) 

There is no coinbase proof requirement, no check that `tx_id` is a leaf node rather than an internal node, and no enforcement that callers must use v2. The `ProofArgs` struct accepted by v1 has no `coinbase_tx_id` or `coinbase_merkle_proof` fields at all: [4](#0-3) 

The analog to H-04 is exact: just as an insider could call `unlock()` without first calling `deposit()` — bypassing the prerequisite that was supposed to protect the system — any NEAR caller can invoke `verify_transaction_inclusion` without supplying a coinbase proof, bypassing the prerequisite that v2 was supposed to enforce.

---

### Impact Explanation

Any downstream NEAR contract (cross-chain bridge, token minter, escrow) that calls `verify_transaction_inclusion` and acts on a `true` result can be deceived into accepting a forged transaction inclusion proof. The attacker does not need to mine a block or control any privileged role. They only need:

1. A real, confirmed Bitcoin block whose hash is in `mainchain_header_to_height` (i.e., on the canonical chain with sufficient confirmations).
2. A 64-byte value `F` that equals an internal Merkle tree node of that block.
3. A valid Merkle sibling path from `F` to the block's `merkle_root`.

Calling `verify_transaction_inclusion` with `tx_id = F`, the correct `tx_block_blockhash`, a valid `tx_index` matching the internal node's position, and the sibling path causes the function to return `true` — certifying that a transaction that was never broadcast or mined is "included" in the block. [5](#0-4) 

The corrupted value is the **proof result** (`bool`) returned to the consuming contract. Any authorization, fund release, or state change gated on this result is exploitable.

---

### Likelihood Explanation

- The entry point is public and requires no special role, stake, or deposit.
- The 64-byte forgery technique is publicly documented and tooled.
- Any relayer or observer of the Bitcoin chain has all the information needed (block headers, Merkle trees) to construct the attack inputs.
- The only prerequisite is that the target block is already accepted into the canonical chain — a condition that is always true for any block the attacker wants to forge inclusion against.

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or gate it behind an access-control role so it cannot be called externally. Alternatively, delete the function body and replace it with a hard `panic!("use verify_transaction_inclusion_v2")`. The internal call from v2 should be refactored into a private helper so the v1 public entry point can be fully closed. [6](#0-5) 

---

### Proof of Concept

**Setup**: Block `B` is on the canonical chain. Its Merkle tree has internal node `N` at position `p` (where `N` is the SHA256d of two 32-byte child hashes, making `N` representable as a 64-byte "transaction").

**Attack**:
```
verify_transaction_inclusion(ProofArgs {
    tx_id:              N,          // internal node hash, not a real tx
    tx_block_blockhash: B,          // real canonical block
    tx_index:           p,          // position of N in the tree
    merkle_proof:       [siblings], // valid sibling path from N to merkle_root(B)
    confirmations:      1,
})
```

**Result**: The function computes `compute_root_from_merkle_proof(N, p, siblings)` which equals `merkle_root(B)`, so it returns `true`. [5](#0-4) 

The consuming contract receives `true` and releases funds / mints tokens for a Bitcoin transaction that does not exist. The v2 coinbase check would have caught this because the coinbase proof would not be consistent with `N` being a leaf, but v1 has no such check and remains directly callable.

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
