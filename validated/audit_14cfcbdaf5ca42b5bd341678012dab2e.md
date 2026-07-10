### Title
`verify_transaction_inclusion` Is Callable By Anyone, Bypassing Coinbase Merkle Proof Forgery Mitigation - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` remains a fully public NEAR contract method despite being deprecated in favour of `verify_transaction_inclusion_v2`, which adds the coinbase Merkle proof check that mitigates the 64-byte transaction Merkle proof forgery vulnerability. Because no access-control guard prevents direct external calls, any unprivileged NEAR account can invoke the unprotected function and obtain a `true` SPV-proof result for a forged `tx_id` (an internal Merkle-tree node), bypassing the only on-chain defence against that attack class.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte Merkle proof forgery hole. Its logic is:

1. Validate that the coinbase transaction sits at index 0 and its Merkle proof reconstructs the block's `merkle_root`.
2. Only then delegate to `verify_transaction_inclusion` for the actual tx proof. [1](#0-0) 

`verify_transaction_inclusion` itself performs no such coinbase check. Its own documentation acknowledges the gap:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [2](#0-1) 

The function is marked `#[deprecated]` at the Rust source level, but that attribute only emits compiler warnings for Rust callers. [3](#0-2) 

It is still declared `pub` inside a `#[near]` impl block, which means the NEAR runtime exposes it as a callable contract method. Any external account — including an adversarial NEAR contract — can invoke it directly via a cross-contract call or a plain JSON-RPC `view` call, completely skipping the coinbase validation gate in `verify_transaction_inclusion_v2`. [4](#0-3) 

The structural parallel to the reported bug is exact:

| Vader finding | This repository |
|---|---|
| `VaderRouter.addLiquidity()` performs validation, then calls `BasePool.mint()` | `verify_transaction_inclusion_v2` performs coinbase validation, then calls `verify_transaction_inclusion` |
| `BasePool.mint()` is `public` without `onlyRouter` | `verify_transaction_inclusion` is `pub` without any caller restriction |
| Anyone can call `mint()` directly, bypassing router validation | Anyone can call `verify_transaction_inclusion` directly, bypassing coinbase proof validation |

---

### Impact Explanation

A recipient NEAR contract that calls `verify_transaction_inclusion` to gate a privileged action (e.g., releasing bridged funds, minting wrapped tokens, settling a bet) can be deceived. An attacker constructs a `ProofArgs` where `tx_id` is the hash of an internal Merkle-tree node rather than a real transaction. `verify_transaction_inclusion` recomputes the Merkle root from that node and the supplied proof path; if the path is crafted correctly the root matches the stored block header's `merkle_root`, and the function returns `true` for a transaction that was never broadcast or confirmed on Bitcoin. [5](#0-4) 

The CLAUDE.md developer notes confirm the vulnerability is real and known:

> "This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash." [6](#0-5) 

---

### Likelihood Explanation

The attack requires no privileged role, no leaked key, and no social engineering. Any NEAR account can issue a cross-contract call or a direct RPC call to `verify_transaction_inclusion`. The 64-byte Merkle forgery technique is publicly documented (BitMEX research, referenced in the contract's own doc-comment at line 268). The only barrier is constructing the forged proof, which is a known, mechanically reproducible procedure for any block whose Merkle tree has more than one transaction. [7](#0-6) 

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` (make it `pub(crate)` or `fn`) so it is no longer reachable as an on-chain entry point. All external callers must be directed to `verify_transaction_inclusion_v2`. Alternatively, add an explicit `#[private]` attribute (NEAR SDK) to prevent cross-contract calls, though making the function non-public is the cleaner fix since `verify_transaction_inclusion_v2` already delegates to it internally.

---

### Proof of Concept

1. Attacker identifies a confirmed Bitcoin block `B` with at least two transactions, stored in the light-client contract.
2. Attacker computes an internal Merkle-tree node `N` (the hash of two sibling leaf hashes) and constructs a `merkle_proof` path such that `compute_root_from_merkle_proof(N, index, proof) == B.merkle_root`.
3. Attacker calls `verify_transaction_inclusion` directly (bypassing `verify_transaction_inclusion_v2`) with `tx_id = N`, `tx_block_blockhash = B`, and the crafted proof.
4. The function returns `true` — confirming a Bitcoin transaction that does not exist.
5. Any recipient NEAR contract that trusts this result executes its privileged action (fund release, token mint, etc.) based on a forged proof. [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L265-268)
```rust
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
```

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

**File:** contract/src/lib.rs (L346-368)
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
```

**File:** contract/CLAUDE.md (L64-67)
```markdown
`verify_transaction_inclusion(ProofArgs)` — SPV proof: given a tx hash, block hash, and merkle proof, verifies the transaction is in the block by recomputing the merkle root.

**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.

```
