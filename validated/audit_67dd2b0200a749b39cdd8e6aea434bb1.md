### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Validation — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (the deprecated v1 path) and `verify_transaction_inclusion_v2` (the secure path) are two parallel, publicly callable entry points for the same operation. The v2 path adds a mandatory coinbase Merkle proof check to prevent 64-byte transaction Merkle proof forgery. The v1 path omits this check entirely and remains fully reachable by any unprivileged NEAR caller, allowing an attacker to bypass the security fix and obtain a `true` verification result for a forged transaction inclusion proof.

---

### Finding Description

The contract exposes two public methods for transaction inclusion verification:

**`verify_transaction_inclusion` (v1)** — marked `#[deprecated]` but still callable: [1](#0-0) 

It performs only:
1. Confirmation count check
2. Mainchain membership check
3. Merkle proof computation against `header.block_header.merkle_root`

It does **not** validate a coinbase Merkle proof.

**`verify_transaction_inclusion_v2`** — the secure replacement: [2](#0-1) 

It adds a mandatory coinbase proof check before delegating to v1:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [3](#0-2) 

The v1 function carries no access control beyond `#[pause]` — no `#[private]`, no `#[trusted_relayer]`, no role gate: [4](#0-3) 

This is structurally identical to the M-7 pattern: the "old" code path (`LesApiBackend::SendTx`) omits a critical step that the "new" code path (`EthAPIBackend::SendTx`) performs. Here, the old path omits the coinbase proof check that the new path enforces.

---

### Impact Explanation

The 64-byte transaction Merkle proof forgery attack (documented at https://www.bitmex.com/blog/64-Byte-Transactions) allows an attacker to construct a `tx_id` that is actually an internal Merkle tree node (64 bytes), and supply a Merkle proof path that makes it appear to be a leaf. Without the coinbase proof check, the contract cannot distinguish a real transaction hash from an internal node hash.

By calling `verify_transaction_inclusion` directly with a crafted `tx_id` (an internal node hash) and a valid Merkle path, an attacker can cause the function to return `true` for a transaction that was never included in the block. Any downstream NEAR contract that consumes this `true` result — e.g., to release funds, mint tokens, or confirm a cross-chain action — is deceived into accepting a forged proof.

The corrupted value is the `bool` return of `verify_transaction_inclusion`: it returns `true` for a forged inclusion proof when the correct answer is `false`.

---

### Likelihood Explanation

The entry path requires no privilege. Any NEAR account can call `verify_transaction_inclusion` directly with adversarially crafted `ProofArgs`. The `#[deprecated]` Rust attribute is a compiler hint only; it does not prevent on-chain invocation. The function is compiled into the contract and is fully reachable via NEAR RPC. The attacker only needs to:

1. Identify a real Bitcoin block on the mainchain tracked by the contract.
2. Construct a 64-byte internal node hash that satisfies the Merkle path to the block's `merkle_root`.
3. Call `verify_transaction_inclusion` with that crafted `tx_id`, `tx_index`, and `merkle_proof`.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with `#[private]` to prevent external calls. If backward compatibility must be preserved, add the same coinbase proof requirement to v1, or redirect v1 to panic unconditionally with a migration message. The deprecation marker alone is insufficient as a security boundary.

---

### Proof of Concept

1. A real Bitcoin block `B` is on the contract's mainchain with `merkle_root = R`.
2. Attacker finds two 32-byte values `L` and `R_node` such that `SHA256d(L || R_node) == R` (i.e., an internal node that hashes to the root). This is the 64-byte forgery: `tx_id = SHA256d(L || R_node)` with an empty or single-element proof path.
3. Attacker calls `verify_transaction_inclusion` with:
   - `tx_id` = the forged internal node hash
   - `tx_block_blockhash` = block `B`'s hash
   - `tx_index` = 0
   - `merkle_proof` = `[]` (empty, since the forged hash is the root itself)
   - `confirmations` = 1
4. The function computes `compute_root_from_merkle_proof(tx_id, 0, &[])` which returns `tx_id` directly, and compares it to `header.block_header.merkle_root`. [5](#0-4) 
5. If the forged `tx_id` equals `merkle_root`, the function returns `true` — a false positive for a transaction that does not exist.
6. `verify_transaction_inclusion_v2` would have rejected this at step 3 by requiring a valid coinbase proof, which the attacker cannot produce for a forged root. [3](#0-2)

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
