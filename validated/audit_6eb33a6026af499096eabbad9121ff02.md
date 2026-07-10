### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Proof Guard Against 64-Byte Merkle Forgery — (`File: contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` was deprecated in favour of `verify_transaction_inclusion_v2`, which adds a mandatory coinbase Merkle proof check to block the well-known 64-byte transaction forgery attack. However, the deprecated function is still `pub` on the NEAR contract and is only gated by `#[pause]`. Any unprivileged NEAR caller can invoke it directly, skipping the coinbase guard entirely and obtaining a `true` SPV-inclusion result for a transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte Merkle forgery vector documented at https://www.bitmex.com/blog/64-Byte-Transactions. It enforces two things the old function does not:

1. The coinbase transaction must be proven to sit at index 0 of the same Merkle tree.
2. The coinbase proof and the target-transaction proof must have equal depth. [1](#0-0) 

After those checks pass, `verify_transaction_inclusion_v2` delegates to the old function via `args.into()`. [2](#0-1) 

The old function, however, is still `pub` and carries only a Rust `#[deprecated]` attribute and a `#[pause]` gate: [3](#0-2) 

`#[deprecated]` is a **compile-time lint** for Rust callers. It has zero effect on NEAR RPC callers: the method is exported in the contract ABI and callable by any account. The `#[pause]` gate only blocks calls when the contract is administratively paused; it imposes no caller-identity restriction.

The function itself acknowledges the risk in its own doc comment: [4](#0-3) 

The `ProofArgs` struct accepted by the old function contains no `coinbase_tx_id` or `coinbase_merkle_proof` fields, so the coinbase check is structurally impossible to perform through this path: [5](#0-4) 

---

### Impact Explanation

A downstream NEAR contract or application that calls `verify_transaction_inclusion` (or that a user tricks into calling it) will receive `true` for a fabricated Bitcoin transaction. The corrupted invariant is the contract's core guarantee: **a `true` return means the supplied `tx_id` is a real transaction committed to a confirmed Bitcoin block**. With the old entry point open, that guarantee is broken. Any bridge, escrow, or payment-proof system built on top of this contract can be defrauded.

---

### Likelihood Explanation

The 64-byte Merkle forgery technique is publicly documented and has known tooling. No privileged role, leaked key, or social engineering is required — only a valid NEAR account and knowledge of a confirmed block's Merkle root. The attacker constructs a 64-byte blob whose double-SHA256 hash chains to the target `merkle_root` through a crafted proof path, then calls `verify_transaction_inclusion` directly. The contract's own comment confirms the function is vulnerable to exactly this input.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or restrict it to an internal (`fn`, not `pub fn`) helper callable only from `verify_transaction_inclusion_v2`. A Rust `#[deprecated]` attribute does not restrict on-chain access. The safe path is to make the function private:

```rust
// Change:
pub fn verify_transaction_inclusion(&self, ...) -> bool { ... }
// To:
fn verify_transaction_inclusion(&self, ...) -> bool { ... }
```

If backward compatibility with existing callers must be preserved temporarily, add an explicit `#[private]` NEAR SDK attribute so that only the contract itself can invoke the method.

---

### Proof of Concept

1. The attacker identifies a confirmed Bitcoin block whose hash `B` is in the contract's mainchain (verifiable via `get_block_hash_by_height`).
2. Using the known 64-byte Merkle forgery technique, the attacker constructs a 64-byte value `fake_tx` and a `merkle_proof` path such that `compute_root_from_merkle_proof(fake_tx, idx, proof) == merkle_root_of_B`. [6](#0-5) 
3. The attacker calls `verify_transaction_inclusion` directly via NEAR RPC with `ProofArgs { tx_id: fake_tx, tx_block_blockhash: B, tx_index: idx, merkle_proof: proof, confirmations: 1 }`. [7](#0-6) 
4. The contract computes the Merkle root, finds it matches `B`'s stored `merkle_root`, and returns `true` — confirming inclusion of a transaction that never existed on Bitcoin.
5. The coinbase guard in `verify_transaction_inclusion_v2` is never reached. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L278-279)
```rust
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
