### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` but remains a live, publicly callable NEAR method. It is also used as an internal subroutine by `verify_transaction_inclusion_v2`, which calls it after performing a coinbase Merkle proof check. The same function therefore carries two distinct semantic roles — a public endpoint (insecure, no coinbase guard) and a trusted internal subroutine (secure, coinbase already validated by the caller). Any unprivileged NEAR account can invoke the public role directly, bypassing the coinbase check entirely and exploiting the documented 64-byte transaction Merkle proof forgery vulnerability.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte Merkle forgery attack (referenced in the code's own docstring and linked to the BitMEX disclosure). It enforces a coinbase Merkle proof before delegating to the old function: [1](#0-0) 

The delegation on line 368 is:

```rust
self.verify_transaction_inclusion(args.into())
```

This is safe only because `verify_transaction_inclusion_v2` has already validated the coinbase proof at lines 358–365. The old function itself performs no such check: [2](#0-1) 

The function is decorated `#[deprecated]` and carries an explicit `# Warning` in its docstring:

> *"This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."*

Despite this, it retains `pub` visibility and the `#[pause]` gate — meaning it is part of the live on-chain ABI and callable by any unprivileged NEAR account whenever the contract is not paused.

The semantic overloading is exact: the same function body serves as (1) a standalone public verification endpoint with no coinbase guard, and (2) a trusted internal subroutine invoked after the coinbase guard has already been satisfied. There is no enforcement mechanism distinguishing these two call contexts.

---

### Impact Explanation

An attacker constructs a 64-byte value that is a valid internal Merkle tree node for a real block. They submit it as `tx_id` to `verify_transaction_inclusion` with a valid `merkle_proof` path. `compute_root_from_merkle_proof` will compute the correct Merkle root from this internal node, and the function returns `true` — certifying inclusion of a transaction that does not exist. [3](#0-2) 

Any recipient contract that gates a payment, bridge withdrawal, or state transition on a `true` result from `verify_transaction_inclusion` is deceived into accepting a forged proof. The corrupted value is the contract's proof verification result: a `bool` that downstream logic treats as a canonical inclusion guarantee.

---

### Likelihood Explanation

- The attack is fully documented in the code's own docstring and in the linked BitMEX post.
- The entry path requires no privileged role, no leaked key, and no social engineering — only a direct NEAR function call.
- The only barrier is knowing to call the deprecated endpoint instead of v2; this is trivially discoverable from the on-chain ABI or the public source.
- The contract is deployed on NEAR mainnet/testnet and the method is reachable whenever the contract is unpaused.

Likelihood: **High**.

---

### Recommendation

Remove `pub` from `verify_transaction_inclusion` or change its visibility to `pub(crate)` so it can only be called internally by `verify_transaction_inclusion_v2`. This eliminates the dual-role ambiguity: the function becomes a private subroutine with a single, well-defined semantic (post-coinbase-validation helper), and the forgery entry point disappears from the public ABI.

---

### Proof of Concept

1. Identify a real block accepted by the light client with a known Merkle tree of ≥ 2 transactions.
2. Compute an internal Merkle node `N` at depth 1: `N = double_sha256(tx0 || tx1)`.
3. Construct a `merkle_proof` path from `N` to the Merkle root (one element shorter than a leaf proof).
4. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_index = 0`, and the constructed `merkle_proof`.
5. The function computes `compute_root_from_merkle_proof(N, 0, proof)` which equals the block's real Merkle root, and returns `true` — despite `N` not being any real transaction hash. [4](#0-3)

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
