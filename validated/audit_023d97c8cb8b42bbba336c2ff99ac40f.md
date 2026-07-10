### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` (v1) function is still a publicly reachable NEAR entry point. It accepts an arbitrary `tx_id` without validating that it is a Merkle-tree leaf (a real transaction hash) rather than an internal node. An unprivileged caller can supply a crafted internal-node hash as `tx_id` together with a matching sibling path, causing the function to return `true` for a transaction that was never included in the block.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to mitigate the well-known 64-byte Merkle-proof forgery attack (https://www.bitmex.com/blog/64-Byte-Transactions). The v2 function adds a coinbase-proof check of equal length to make forgery harder. However, the v1 function was only annotated `#[deprecated]`—a Rust compiler hint that carries no runtime enforcement—and remains a fully callable public method on the NEAR contract.

The v1 function's own documentation acknowledges the flaw:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification." [1](#0-0) 

The verification logic itself is:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

`compute_root_from_merkle_proof` treats its first argument as an opaque 32-byte value and hashes it upward through the sibling path without any leaf-vs-internal-node distinction: [3](#0-2) 

Because Bitcoin's Merkle tree uses the same `double_sha256` operation for both leaf and internal nodes, any internal node at depth *k* can be presented as a "transaction hash" with a *k*-element sibling path that correctly reconstructs the root. The only guard in v1 is `require!(!args.merkle_proof.is_empty())`, which an attacker trivially satisfies. [4](#0-3) 

The v2 function does not remove v1; it calls it internally after its own coinbase check:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [5](#0-4) 

So v1 is reachable both directly (any caller) and indirectly (through v2).

---

### Impact Explanation

**Impact: Medium.**

Any downstream NEAR contract or off-chain application that calls `verify_transaction_inclusion` to gate a financial or state-changing action (e.g., releasing bridged funds, confirming a deposit) can be deceived into accepting a forged proof. The function returns `bool`; a `true` result is indistinguishable from a legitimately verified inclusion. The corrupted value is the **proof result** returned to the caller.

---

### Likelihood Explanation

**Likelihood: Medium.**

The 64-byte Merkle forgery attack is publicly documented and requires only knowledge of a block's Merkle tree structure (obtainable from any Bitcoin full node or block explorer). No privileged role, private key, or social engineering is needed. The attacker only needs to be a standard NEAR account able to call a public contract method.

---

### Recommendation

1. **Remove `verify_transaction_inclusion` (v1) entirely**, or gate it with an `env::panic_str` body so it is unreachable at runtime, rather than relying on a compiler-only `#[deprecated]` annotation.
2. If backward compatibility is required, add the same coinbase-proof length and validity check that v2 performs before delegating to the Merkle root comparison.
3. Document explicitly in the ABI/README that v1 is unsafe and must not be called by any integrating contract.

---

### Proof of Concept

1. Identify any confirmed mainchain block `B` with at least two transactions. Obtain its full transaction list and Merkle tree.
2. Select the internal node `N` at depth 1 (the hash of `tx[0]` and `tx[1]`). Compute the sibling path from `N` up to the Merkle root (this is a standard `(depth - 1)`-element path).
3. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash)
   - `tx_block_blockhash = B`
   - `tx_index` = the position of `N` among its siblings at depth 1
   - `merkle_proof` = the sibling path from depth 1 to the root
   - `confirmations = 1`
4. The function returns `true`, falsely asserting that `N` is an included transaction in block `B`. [6](#0-5)

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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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
