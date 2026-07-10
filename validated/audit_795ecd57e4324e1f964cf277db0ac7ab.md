### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Validation — (File: `contract/src/lib.rs`)

### Summary
The contract exposes two transaction inclusion verification functions. `verify_transaction_inclusion_v2` was introduced specifically to mitigate the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase Merkle proof. However, the original `verify_transaction_inclusion` — explicitly documented as vulnerable and deprecated since v0.5.0 — remains publicly callable by any unprivileged NEAR account with no additional access restriction. Any caller can invoke the deprecated path directly, bypassing the coinbase proof check entirely and obtaining a `true` result for a forged transaction inclusion proof.

### Finding Description
`verify_transaction_inclusion` is decorated only with `#[pause]` and carries no role-based access control or caller restriction: [1](#0-0) 

The contract's own warning block explicitly states the broken invariant: [2](#0-1) 

The secure replacement, `verify_transaction_inclusion_v2`, enforces an additional coinbase Merkle proof check before delegating to the deprecated function: [3](#0-2) 

Because the deprecated function is still a live public entry point, the coinbase proof guard introduced in v2 can be bypassed entirely by calling the old function directly. The Merkle root comparison in the deprecated path: [4](#0-3) 

accepts any `tx_id` — including an internal Merkle tree node — as long as the supplied proof path reconstructs the correct root. This is the exact 64-byte transaction forgery attack described at https://www.bitmex.com/blog/64-Byte-Transactions.

### Impact Explanation
Any protocol or NEAR smart contract that calls `verify_transaction_inclusion` (or is directed to call it by an attacker) will receive `true` for a fabricated transaction inclusion claim. An attacker can construct a `tx_id` that is an internal 32-byte Merkle tree node, build a valid proof path from that node to the block's Merkle root, and have the contract confirm the "transaction" as included. This corrupts the proof result returned to any consumer of the light client, enabling fraud in cross-chain bridges, payment verification systems, or any protocol that gates actions on a `true` return value from this contract.

### Likelihood Explanation
The function is publicly callable by any NEAR account with no stake, role, or permission requirement. The only gate is the global pause, which is not active under normal operation. The 64-byte Merkle forgery technique is well-documented and the attack inputs are straightforward to construct from any Bitcoin block's Merkle tree. Any integrator that reads the contract ABI and calls the deprecated function — or any attacker who can influence which function a downstream contract calls — can trigger this.

### Recommendation
Remove `verify_transaction_inclusion` from the public interface entirely, mirroring the resolution applied in the external report (removing the `exit()` escape hatch). If backward compatibility is required during a transition period, gate the deprecated function behind a privileged role (e.g., `Role::DAO`) so it cannot be called by unprivileged accounts, and document a sunset block height after which it will be removed.

### Proof of Concept

1. Identify any Bitcoin block already accepted into the contract's mainchain (its hash is in `mainchain_header_to_height`).
2. Obtain the block's full transaction list and Merkle tree. Select any internal node `N` at depth `d` whose concatenated left+right child hashes form a 64-byte value interpretable as a valid-looking transaction.
3. Construct a Merkle proof path from `N` up to the Merkle root (length `d` hashes).
4. Call the deprecated public entry point directly:
   ```
   verify_transaction_inclusion({
       tx_id: <hash of internal node N>,
       tx_block_blockhash: <accepted block hash>,
       tx_index: <index consistent with proof path>,
       merkle_proof: <proof path from N to root>,
       confirmations: 1,
   })
   ```
5. The function computes `compute_root_from_merkle_proof(N, index, proof)`, which equals the block's real Merkle root, and returns `true` — falsely confirming that the internal node is an included transaction, with no coinbase proof ever checked. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L277-280)
```rust
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
