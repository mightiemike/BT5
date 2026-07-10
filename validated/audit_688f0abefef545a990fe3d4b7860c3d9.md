### Title
Deprecated `verify_transaction_inclusion` Bypasses Coinbase Merkle Proof Validation, Enabling Forged Transaction Inclusion Proofs — (File: `contract/src/lib.rs`)

---

### Summary

The v1 `verify_transaction_inclusion` function remains callable by any unprivileged NEAR account despite being deprecated. It omits the coinbase Merkle proof check that `verify_transaction_inclusion_v2` performs to block the 64-byte transaction Merkle-proof forgery attack. An attacker can supply an internal Merkle-tree node hash as `tx_id` together with a crafted proof that still resolves to the block's real Merkle root, causing the function to return `true` for a transaction that does not exist. Consumer contracts (bridges, atomic-swap protocols, cross-chain lenders) that gate fund releases on this return value are therefore exploitable by any unprivileged caller.

---

### Finding Description

`verify_transaction_inclusion` (v1) is still a live, publicly callable entry point:

```
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
``` [1](#0-0) 

The function's only structural guard is that the Merkle proof must be non-empty:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [2](#0-1) 

It then computes the Merkle root from the caller-supplied `tx_id` and `merkle_proof` and compares it to the stored block header's `merkle_root`:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [3](#0-2) 

There is **no check** that `args.tx_id` is a leaf-level transaction hash rather than an internal Merkle-tree node. Bitcoin's Merkle tree does not distinguish between leaf and internal nodes; an internal node hash is itself a valid 32-byte value. An attacker can therefore supply an internal node hash as `tx_id` with a correspondingly shorter proof path that still hashes up to the real `merkle_root`, and the function returns `true`.

The v2 function prevents this by first verifying that the coinbase transaction (always at index 0, always a leaf) produces the same Merkle root with a proof of the **same length** as the target proof, bounding the target to the same tree depth:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [4](#0-3) 

The v1 function has no equivalent guard. The contract's own documentation acknowledges the gap:

> *This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.* [5](#0-4) 

The analog to the external report is direct: in the HydraDX finding, a `safe_withdrawal` flag bypasses the price-barrier check, allowing 100 % of a user's liquidity to be taken as fees. Here, the v1 function is the "bypass mode" — it omits the coinbase-proof security check — and any unprivileged caller can invoke it to obtain a fraudulent `true` result, just as any user could trigger the fee-draining path once the tradability flag was set.

---

### Impact Explanation

Any NEAR smart contract (bridge, atomic-swap, cross-chain lending protocol) that calls `verify_transaction_inclusion` and releases funds or updates state on a `true` return can be drained by an attacker who forges a proof for a Bitcoin transaction that never existed. The light client is explicitly described as "a foundational layer for bridges, atomic swaps, and cross-chain lending protocols," so the downstream blast radius is the entire class of consumer contracts built on top of it. [6](#0-5) 

---

### Likelihood Explanation

- The function is callable by **any** unprivileged NEAR account (only `#[pause]` gating, no role check).
- The required inputs — a valid mainchain block hash and its Merkle tree structure — are fully public on the Bitcoin blockchain.
- The 64-byte transaction forgery technique is well-documented and tooling exists to construct such proofs.
- The v1 function is not removed, only deprecated, so it remains in the deployed contract's ABI indefinitely until an upgrade explicitly drops it.

---

### Recommendation

1. **Remove** `verify_transaction_inclusion` (v1) from the contract's public interface in the next upgrade, or add a hard `require!(false, "use verify_transaction_inclusion_v2")` to make it unconditionally revert.
2. If backward compatibility is required, retrofit the coinbase-proof check from v2 into v1 before any Merkle-root comparison.
3. Audit all known consumer contracts to confirm they have migrated to `verify_transaction_inclusion_v2`.

---

### Proof of Concept

1. Pick any confirmed mainchain block `B` whose Merkle tree has depth ≥ 2. Let its `merkle_root` be `R`.
2. Identify an internal node `N` at depth 1 (the hash of two leaf transaction hashes). `N` is 32 bytes and publicly computable from the block's transaction list.
3. Construct `merkle_proof = [sibling_of_N_at_depth_1]` (a single-element proof) and set `tx_index` to the appropriate position.
4. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_block_blockhash = hash(B)`, `tx_index`, `merkle_proof`, `confirmations = 1`.
5. `compute_root_from_merkle_proof(N, tx_index, [sibling]) == R` → function returns **`true`**.
6. A consumer contract gating a fund release on this call now releases funds for a Bitcoin transaction that does not exist. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L263-323)
```rust
    /// Verifies that a transaction is included in a block at a given block height
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
