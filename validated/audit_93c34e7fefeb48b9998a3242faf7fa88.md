### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase Merkle Proof Guard Introduced in v2 - (File: `contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` (v1) is still a live, publicly callable NEAR function despite being deprecated. Any unprivileged NEAR caller can invoke it directly, completely bypassing the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` added specifically to mitigate the 64-byte transaction Merkle proof forgery vulnerability. This is the direct analog of the Safe wallet bug: just as a wallet owner could call `disableModule()` to remove the fee-taking guard, any caller here can route around the security guard by calling the old entry point.

### Finding Description

`verify_transaction_inclusion_v2` was introduced to close the 64-byte transaction Merkle proof forgery attack (documented at https://www.bitmex.com/blog/64-Byte-Transactions). Its guard is the coinbase Merkle proof check: [1](#0-0) 

After this check passes, v2 delegates to v1: [2](#0-1) 

However, v1 is still a fully public, non-private NEAR function: [3](#0-2) 

The contract's own documentation acknowledges the forgery risk in v1: [4](#0-3) 

The only guard in v1 is that the Merkle proof must be non-empty: [5](#0-4) 

There is no `#[private]` attribute, no role check, and no `#[trusted_relayer]` restriction on v1. Any NEAR account can call it.

### Impact Explanation

A downstream bridge or application contract that calls `verify_transaction_inclusion` (v1) — or that an attacker tricks into calling it — receives a `true` result for a forged proof. Specifically, an attacker can supply an internal Merkle tree node (which is always 64 bytes: two concatenated 32-byte SHA256d hashes) as the `tx_id`. Without the coinbase proof check, the contract cannot distinguish this internal node from a real transaction hash. A `true` return value from the light client is the authoritative signal used by bridge contracts to release funds or confirm cross-chain state. A forged `true` result corrupts the canonical proof-verification outcome that the entire system is built on.

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion` directly. Existing integrations that have not yet migrated to v2 are immediately exposed. New integrations that accidentally use v1 (e.g., by copying older documentation or examples) are also exposed. The 64-byte forgery technique is publicly documented and well understood.

### Recommendation

Remove the `#[near]` / public export of `verify_transaction_inclusion` entirely, or convert it to a private internal helper (`fn verify_transaction_inclusion_inner`). All external callers must be forced through `verify_transaction_inclusion_v2`. If backward compatibility is required for a transition period, the v1 public function should at minimum be gated with `#[private]` so only the contract itself can call it (as v2 already does internally via `self.verify_transaction_inclusion`).

### Proof of Concept

1. Attacker identifies a real Bitcoin block whose Merkle tree has an internal node `N` (32 bytes) that they wish to "prove" is a transaction.
2. Attacker constructs a valid Merkle sibling path from `N` up to the block's `merkle_root` (this path is computable from public block data).
3. Attacker calls `verify_transaction_inclusion` directly on the NEAR contract:
   ```
   verify_transaction_inclusion({
     tx_id: N,                        // internal node, not a real tx
     tx_block_blockhash: <real hash>, // block is in mainchain
     tx_index: <index of N>,
     merkle_proof: <siblings from N to root>,
     confirmations: 1
   })
   ```
4. `compute_root_from_merkle_proof(N, index, siblings)` returns the block's real `merkle_root`.
5. The function returns `true`.
6. Any bridge contract that called this function now believes the forged "transaction" is confirmed on Bitcoin and releases funds or updates cross-chain state accordingly.

The coinbase proof check in v2 — which would have caught this by requiring a separate proof that the coinbase tx hashes to the same root — is never executed because v1 was called directly. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

**File:** contract/src/lib.rs (L317-323)
```rust
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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```
