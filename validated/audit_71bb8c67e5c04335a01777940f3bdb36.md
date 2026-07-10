### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Validation Required to Prevent 64-Byte Transaction Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, unprivileged-callable NEAR entry point despite being deprecated in favour of `verify_transaction_inclusion_v2`. The v2 upgrade was introduced specifically to enforce coinbase merkle proof validation and close the 64-byte transaction Merkle proof forgery vulnerability. Because v1 omits that validation step and remains reachable, any caller can obtain a `true` proof result for a forged transaction inclusion claim, bypassing the security requirement the codebase itself documents.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced with an explicit security rationale:

> "Verifies that a transaction is included in a block at a given block height, with an additional coinbase merkle proof validation. This is needed to mitigate the 64-byte transaction Merkle proof forgery vulnerability: https://www.bitmex.com/blog/64-Byte-Transactions" [1](#0-0) 

The v2 function enforces this by requiring that the coinbase transaction's merkle proof matches the block's merkle root before proceeding:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [2](#0-1) 

The v1 function, however, performs no such check. It computes only the transaction's own merkle path and compares it to the block's merkle root:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [3](#0-2) 

Critically, v1 is still decorated with `#[pause]` (not `#[private]`, not removed), meaning it is a fully reachable public NEAR method. The `#[deprecated]` Rust attribute is a compile-time lint only; it imposes no runtime restriction whatsoever. Any NEAR account can call `verify_transaction_inclusion` directly at any time. [4](#0-3) 

The v1 function's own docstring acknowledges the vulnerability it leaves open:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [5](#0-4) 

The business requirement — that all transaction inclusion verification must include coinbase proof validation — is implemented in v2 but is not enforced at the protocol level. The old, insecure path remains open.

---

### Impact Explanation

An attacker can craft a 64-byte value that is the hash of an internal Merkle tree node (not a real transaction) and submit it as `tx_id` to `verify_transaction_inclusion`. Because v1 does not anchor the proof to the coinbase transaction, the computed root can match the block's `merkle_root` for a fabricated leaf. The function returns `true`. Any downstream dApp, bridge, or contract that calls v1 to gate a financial action (e.g., releasing funds upon BTC transaction confirmation) will be deceived into treating a non-existent transaction as confirmed.

The corrupted value is the **proof result** (`bool` return of `verify_transaction_inclusion`): it is `true` for a forged inclusion claim that v2 would correctly reject.

---

### Likelihood Explanation

The 64-byte transaction Merkle forgery attack is publicly documented (the codebase itself links to the BitMEX writeup). The deprecated v1 function is part of the contract's public ABI and discoverable by any caller inspecting the contract interface. No privileged role, leaked key, or social engineering is required — only knowledge of the attack and the existence of the v1 entry point.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the contract's public interface entirely, or gate it with `#[private]` so it is no longer callable by external accounts. Since v2 is a strict superset of v1's functionality (v2 calls v1 internally after performing the coinbase check), no legitimate caller loses capability. Alternatively, redirect v1 to call v2 internally, requiring the caller to also supply `coinbase_tx_id` and `coinbase_merkle_proof`.

---

### Proof of Concept

1. Attacker identifies a Bitcoin block whose merkle tree has an internal node whose 32-byte hash, when treated as a leaf at some index, produces the correct merkle root (the 64-byte attack construction).
2. Attacker calls `verify_transaction_inclusion` on the NEAR contract with:
   - `tx_id` = the forged internal-node hash
   - `tx_block_blockhash` = the real block hash (which is on the mainchain)
   - `tx_index` = the index that makes the forged proof compute to the correct root
   - `merkle_proof` = the sibling path for the forged leaf
   - `confirmations` = any value ≤ stored depth
3. `verify_transaction_inclusion` returns `true`.
4. A dApp that calls this function to confirm a BTC deposit before releasing funds on NEAR is deceived into releasing funds for a transaction that never occurred. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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

**File:** contract/src/lib.rs (L325-346)
```rust
    /// Verifies that a transaction is included in a block at a given block height,
    /// with an additional coinbase merkle proof validation.
    /// This is needed to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param tx_id transaction identifier
    /// @param tx_block_blockhash block hash at which transaction is supposedly included
    /// @param tx_index index of transaction in the block's tx merkle tree
    /// @param merkle_proof merkle tree path (concatenated LE sha256 hashes) (does not contain initial transaction_hash and merkle_root)
    /// @param coinbase_tx_id coinbase transaction hash
    /// @param coinbase_merkle_proof merkle proof for the coinbase transaction (must have the same length as merkle_proof)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if tx_id is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Panics
    /// - If `merkle_proof` and `coinbase_merkle_proof` have different lengths
    /// - If `tx_block_blockhash` is not found in the headers pool
    /// - If coinbase merkle proof does not match the block's merkle root
    /// - If the required number of confirmations exceeds the number of stored blocks
    /// - If the block does not belong to the current main chain
    /// - If there are not enough confirmed blocks
    #[pause]
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
