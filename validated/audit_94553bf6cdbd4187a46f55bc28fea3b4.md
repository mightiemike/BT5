### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase Merkle Proof Guard — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` was deprecated in favour of `verify_transaction_inclusion_v2`, which adds a coinbase Merkle proof check to block the 64-byte transaction Merkle proof forgery attack. However, the deprecated function is still a live, unrestricted public NEAR method. Any unprivileged caller can invoke it directly, skipping the coinbase proof guard entirely and obtaining a `true` inclusion result for a fabricated transaction.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte Merkle forgery vector. Its guard is the coinbase proof check:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [1](#0-0) 

This guard is the **only** thing preventing the forgery. The old function has no equivalent check:

```rust
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
``` [2](#0-1) 

Rust's `#[deprecated]` attribute is a **compile-time lint only**. It emits a warning to Rust callers but does not remove the function from the compiled WASM binary or from the NEAR contract's public ABI. Any NEAR account can call `verify_transaction_inclusion` as a normal cross-contract or direct call, receiving a `bool` result without the coinbase proof ever being validated.

The function's own doc comment acknowledges the danger:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [3](#0-2) 

`compute_root_from_merkle_proof` in `merkle-tools` simply iterates the supplied proof hashes and returns the final accumulated hash — it has no awareness of whether the starting hash is a real leaf transaction or an internal node:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    ...
    current_hash
}
``` [4](#0-3) 

---

### Impact Explanation

A caller who obtains a `true` result from `verify_transaction_inclusion` can present that result to any downstream NEAR contract that uses this light client for cross-chain settlement, bridge unlocking, or oracle verification. Because the function returns `true` for a fabricated 64-byte internal-node "transaction," the attacker can claim that an arbitrary Bitcoin transaction was confirmed on-chain when it was not. This corrupts the proof-verification result that is the entire security guarantee of the SPV client.

---

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion` directly. The attacker only needs a confirmed Bitcoin block already tracked by the contract (to satisfy the main-chain and confirmation checks) and the ability to construct a 64-byte value whose double-SHA256 hash appears at a valid internal position in that block's Merkle tree — a well-documented, publicly described technique. The deprecated function is permanently reachable until the contract is upgraded.

---

### Recommendation

1. **Preferred**: Remove `verify_transaction_inclusion` from the public ABI entirely. In NEAR/Rust this means either deleting the method or marking it `#[private]`, which restricts calls to the contract itself only.
2. **Alternative**: Add the same coinbase proof check that `verify_transaction_inclusion_v2` performs, making both functions equivalent in security.

---

### Proof of Concept

```
// Attacker selects any confirmed block B already in the contract's mainchain.
// B has merkle_root R and at least two transactions.
//
// Step 1: Find an internal Merkle node N = SHA256d(left || right) such that
//         a valid merkle_proof path from N at some index reaches R.
//         (This is the standard 64-byte forgery construction.)
//
// Step 2: Call verify_transaction_inclusion directly (NOT v2):
//
//   near call <contract> verify_transaction_inclusion \
//     '{"tx_id": "<N>", "tx_block_blockhash": "<B>",
//       "tx_index": <forged_index>, "merkle_proof": [<siblings...>],
//       "confirmations": 1}'
//
// Result: the function returns `true`.
// The coinbase proof check in verify_transaction_inclusion_v2 is never reached.
// The contract has certified inclusion of a transaction that does not exist.
```

The `verify_transaction_inclusion_v2` wrapper that was meant to be the mandatory path is trivially bypassed by calling the deprecated method directly. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
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

**File:** contract/src/lib.rs (L315-323)
```rust
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
