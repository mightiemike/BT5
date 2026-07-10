### Title
Unvalidated `tx_id` in `verify_transaction_inclusion` Enables Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` accepts any 32-byte hash as `tx_id` without validating that it is a leaf node (an actual transaction) in the Merkle tree. An unprivileged NEAR caller can supply an internal Merkle tree node as `tx_id` together with a valid sibling path to the root, causing the function to return `true` for a transaction that does not exist. The contract's own warning comment acknowledges this gap but leaves the function publicly callable.

---

### Finding Description

`verify_transaction_inclusion` in `contract/src/lib.rs` computes a Merkle root from the caller-supplied `tx_id` and `merkle_proof`, then compares the result against the stored `header.block_header.merkle_root`:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

The only guard on `tx_id` is that `merkle_proof` must be non-empty:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [2](#0-1) 

There is no check that `tx_id` is a leaf node rather than an internal Merkle tree node. The code itself documents this gap:

> *This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.* [3](#0-2) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` is a pure hash-path computation; it has no concept of leaf vs. internal nodes:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
``` [4](#0-3) 

The function is marked `#[deprecated]` but remains `pub` with no role restriction and no `#[trusted_relayer]` guard, so any NEAR account can call it at any time the contract is not paused. [5](#0-4) 

`verify_transaction_inclusion_v2` was introduced to close this gap by requiring a valid coinbase proof at index 0 with the same proof depth as the target transaction, which forces the target to be at leaf depth. However, the v1 function is never removed or access-restricted. [6](#0-5) 

---

### Impact Explanation

Any consumer contract that calls `verify_transaction_inclusion` and uses its boolean result to authorize an action (e.g., releasing bridged funds, minting wrapped tokens, or unlocking collateral) can be exploited. The attacker causes the function to return `true` for a transaction that was never broadcast or confirmed on Bitcoin. The corrupted proof result is the exact state value that downstream authorization logic trusts.

---

### Likelihood Explanation

The attack requires no privileges and no cryptographic work. The internal Merkle tree nodes of any Bitcoin block are computable from the public transaction list. The attacker only needs to:

1. Read the block's transaction list from any Bitcoin node or block explorer.
2. Compute the internal node hashes (standard double-SHA256 operations).
3. Submit a NEAR transaction calling `verify_transaction_inclusion` with an internal node as `tx_id`.

The function is callable by any NEAR account whenever the contract is not paused.

---

### Recommendation

1. **Remove or restrict `verify_transaction_inclusion`**: Add a `#[trusted_relayer]` or role guard, or remove the function entirely. A `#[deprecated]` attribute is a compile-time hint only; it provides no runtime protection.
2. **Enforce v2 at the contract level**: Make `verify_transaction_inclusion_v2` the only public entry point for proof verification.
3. **Add a leaf-depth guard**: If v1 must remain, reject calls where `merkle_proof.len()` is inconsistent with the known block transaction count, or require the caller to also supply a coinbase proof as v2 does.

---

### Proof of Concept

Consider a block in the main chain with four transactions `[T1, T2, T3, T4]`:

```
Leaves:    T1        T2        T3        T4
Level 1:      N12=H(T1,T2)      N34=H(T3,T4)
Root:              Root=H(N12,N34)
```

An attacker calls `verify_transaction_inclusion` with:

| Field | Value |
|---|---|
| `tx_id` | `N12` (internal node — not a real transaction) |
| `tx_index` | `0` |
| `merkle_proof` | `[N34]` |
| `tx_block_blockhash` | hash of the target block |
| `confirmations` | `1` |

`compute_root_from_merkle_proof(N12, 0, [N34])` computes `H(N12, N34) = Root`, which equals `header.block_header.merkle_root`. The function returns `true`.

The consumer contract receives `true` and authorizes an action (e.g., minting wrapped BTC) for a Bitcoin transaction that never existed. [7](#0-6) [8](#0-7)

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
