### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable Without Coinbase Proof Validation, Enabling Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly callable NEAR contract method despite being deprecated. It performs no coinbase Merkle proof validation, making it exploitable by any unprivileged NEAR caller via the well-known 64-byte transaction Merkle proof forgery. Any downstream contract that consumes its `true` result to authorize fund releases can be deceived into accepting a forged inclusion proof.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase Merkle proof of equal depth. The v1 function was marked `#[deprecated]` but was **not removed or disabled at the runtime level**. [1](#0-0) 

The v1 function:
- Carries only a `#[deprecated]` Rust attribute, which is a **compile-time lint warning only** — it does not prevent any NEAR account from calling the method at runtime.
- Accepts a caller-controlled `tx_id`, `tx_block_blockhash`, `tx_index`, and `merkle_proof`.
- Validates only that `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.merkle_root`. [2](#0-1) 

There is **no coinbase proof check** in v1. An attacker can supply an internal Merkle tree node hash as `tx_id` and a valid sibling path, and the computed root will still equal the block's stored `merkle_root`, causing the function to return `true`.

The v2 function closes this by first verifying that the coinbase transaction (at index 0) produces the same Merkle root, which constrains the tree depth and prevents internal-node substitution: [3](#0-2) 

The CLAUDE.md developer documentation explicitly acknowledges the attack surface of v1: [4](#0-3) 

Despite this acknowledgment, v1 remains a callable public method with no runtime guard.

---

### Impact Explanation

The BTC light client is a verification oracle: downstream bridge contracts call `verify_transaction_inclusion` to decide whether to release funds in response to a claimed Bitcoin deposit. If v1 returns `true` for a forged proof, the downstream contract's authorization assumption is broken. An attacker can claim funds for a Bitcoin transaction that never existed, causing direct financial loss to the bridge.

The corrupted value is the **proof result** — `true` is returned for an internal Merkle node that is not a valid transaction, violating the invariant that the function only returns `true` for real, included transactions.

---

### Likelihood Explanation

- **No privileged access required.** The function is decorated only with `#[pause]`; any unprivileged NEAR account can call it when the contract is not paused.
- **Attack is well-documented.** The 64-byte transaction Merkle proof forgery is a known Bitcoin SPV attack (referenced in the codebase itself at line 268).
- **Barrier is low.** The attacker only needs a real Bitcoin block's Merkle tree structure, which is public on-chain data, to construct a valid internal-node proof. [5](#0-4) 

---

### Recommendation

Remove `verify_transaction_inclusion` v1 from the public contract ABI entirely, or add a runtime `panic!`/`env::panic_str` at its entry point so it is unreachable at runtime. Downstream consumers must be migrated to `verify_transaction_inclusion_v2`. A `#[deprecated]` attribute alone provides no on-chain protection.

---

### Proof of Concept

1. Attacker selects any Bitcoin block already stored in the contract's `headers_pool`.
2. Attacker obtains the block's full Merkle tree (public Bitcoin data). They pick any **internal node** `N` at depth `d`.
3. Attacker constructs a sibling path of length `d` from `N` to the Merkle root — this is a valid path by construction.
4. Attacker calls `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real transaction)
   - `tx_block_blockhash` = the real block hash
   - `tx_index` = the index corresponding to `N`'s position
   - `merkle_proof` = the sibling path
5. `compute_root_from_merkle_proof(N, index, siblings)` produces the correct Merkle root.
6. The function returns `true`.
7. A downstream bridge contract that calls v1 to gate a withdrawal now authorizes a payout for a Bitcoin transaction that was never broadcast or confirmed. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L264-323)
```rust
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
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

**File:** contract/CLAUDE.md (L64-67)
```markdown
`verify_transaction_inclusion(ProofArgs)` — SPV proof: given a tx hash, block hash, and merkle proof, verifies the transaction is in the block by recomputing the merkle root.

**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.

```
