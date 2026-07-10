### Title
Merkle Proof Forgery via Missing Leaf-Node Validation in `verify_transaction_inclusion` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is a publicly callable NEAR contract method that accepts an attacker-supplied `tx_id` and `merkle_proof` without validating that `tx_id` is a genuine leaf-level transaction hash. An unprivileged caller can supply an internal Merkle tree node hash as `tx_id` with a crafted shorter proof path, causing the function to return `true` for a transaction that was never included in the block. Any downstream contract that gates asset release on this result is directly exploitable for fund theft.

---

### Finding Description

`verify_transaction_inclusion` delegates proof computation entirely to `compute_root_from_merkle_proof`: [1](#0-0) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` treats whatever hash is passed as `transaction_hash` as the starting node and walks up the tree using the supplied proof path: [2](#0-1) 

There is no check that `tx_id` is a 32-byte leaf (a real serialized transaction hash) rather than an internal Merkle node. The code's own `# Warning` comment acknowledges this: [3](#0-2) 

The `#[deprecated]` Rust attribute only suppresses a compiler warning for Rust callers; it does **not** remove the function from the deployed NEAR contract's ABI. Any NEAR account can call it directly.

The analog to the Shardeum report is exact: just as `signAppData` checked for required fields but not for unexpected extra fields, `verify_transaction_inclusion` checks that `merkle_proof` is non-empty and that the computed root matches, but never validates that the starting hash is a leaf node — allowing the attacker to inject an internal node as the "transaction."

---

### Impact Explanation

This is the well-documented CVE-2017-12842 / "64-byte transaction" Merkle forgery attack. An attacker can:

1. Identify a real Bitcoin block already accepted by the light client.
2. Pick any internal Merkle node `N` at depth `d` in that block's transaction tree.
3. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_index = position_of_N_at_depth_d`, and `merkle_proof = path_from_N_to_root` (length `d`, not the full leaf-to-root length).
4. The function returns `true`.

Any protocol (bridge, escrow, DEX) that calls `verify_transaction_inclusion` to confirm a Bitcoin deposit before releasing NEAR-side assets will be deceived into releasing funds for a transaction that never existed.

---

### Likelihood Explanation

- The function is unconditionally reachable by any unprivileged NEAR account with no staking, role, or deposit requirement beyond gas.
- The attack requires only knowledge of a real block's Merkle tree structure, which is public Bitcoin data.
- The 64-byte transaction technique is publicly documented and tooled.
- Integrators who read the contract ABI see a `verify_transaction_inclusion` method and may use it without reading the deprecation notice embedded in source code.

---

### Recommendation

Remove `verify_transaction_inclusion` from the deployed contract entirely (not just mark it `#[deprecated]`), or add a `require!(false, "use verify_transaction_inclusion_v2")` guard at its entry point so it always panics on-chain. The v2 function already exists and correctly mitigates the attack by requiring a coinbase proof of the same depth: [4](#0-3) 

---

### Proof of Concept

Given a real Bitcoin block `B` accepted by the light client with Merkle root `R` and two transactions `T0`, `T1`:

```
        R
       / \
      N   ...
     / \
    T0  T1
```

The internal node `N = SHA256d(T0 || T1)` is 32 bytes. Call:

```json
{
  "tx_id":            "<hex of N>",
  "tx_block_blockhash": "<hash of B>",
  "tx_index":         0,
  "merkle_proof":     ["<sibling of N at root level>"],
  "confirmations":    1
}
```

`compute_root_from_merkle_proof(N, 0, [sibling])` produces `R`, which equals `header.block_header.merkle_root`. The function returns `true` for the fake transaction `N`. [2](#0-1) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L276-279)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L288-323)
```rust
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
