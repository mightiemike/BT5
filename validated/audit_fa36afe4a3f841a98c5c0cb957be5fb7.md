### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` in Rust but remains a live, callable NEAR entry point. It omits the coinbase Merkle proof check that `verify_transaction_inclusion_v2` adds to close the 64-byte transaction Merkle proof forgery vulnerability. Any unprivileged NEAR caller can supply an internal Merkle tree node hash as `tx_id` with a crafted sibling proof, causing the function to return `true` for a transaction that was never included in the block.

---

### Finding Description

`verify_transaction_inclusion` is documented as vulnerable since v0.5.0: [1](#0-0) 

Despite the deprecation annotation, the function carries `#[pause]` and is a fully reachable NEAR public method. Its only guard against a forged proof is: [2](#0-1) 

It checks that `merkle_proof` is non-empty and that `compute_root_from_merkle_proof(tx_id, tx_index, proof) == header.merkle_root`. It does **not** verify that `tx_id` is a leaf-level transaction rather than an internal Merkle tree node.

`compute_root_from_merkle_proof` in `merkle-tools` is a pure positional hash-chain computation: [3](#0-2) 

Given a real block whose Merkle tree contains internal node `A = hash(tx0, tx1)` at height 1, an attacker can supply:
- `tx_id = A`
- `tx_index = 0` (treating `A` as if it were a leaf at position 0 of a 2-leaf subtree)
- `merkle_proof = [B]` where `B` is `A`'s sibling at the same level

`compute_root_from_merkle_proof(A, 0, [B])` = `hash(A, B)` = real merkle root → function returns `true`.

`verify_transaction_inclusion_v2` closes this by requiring a coinbase proof at position 0 whose length equals the transaction proof length, anchoring the tree to a real leaf: [4](#0-3) 

The deprecated path has no equivalent anchor. The `#[deprecated]` Rust attribute is a compile-time hint; it imposes no runtime restriction on NEAR RPC callers or on downstream NEAR contracts compiled before the deprecation was introduced.

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` (the v1 entry point) to gate a fund release, cross-chain settlement, or state transition can be deceived into accepting a forged Bitcoin transaction inclusion proof. The attacker does not need to forge a Bitcoin block — only a valid proof path within a real, already-confirmed block. The return value `true` is indistinguishable from a legitimate proof result, so the downstream contract has no signal that the proof is forged.

---

### Likelihood Explanation

The 64-byte Merkle proof forgery is a well-documented, publicly known attack (referenced in the contract's own deprecation note and at https://www.bitmex.com/blog/64-Byte-Transactions). The entry point is open to any unprivileged NEAR caller. No privileged role, leaked key, or social engineering is required. Any integrator that did not migrate to `verify_transaction_inclusion_v2` is permanently exposed.

---

### Recommendation

1. Convert `verify_transaction_inclusion` from a deprecated-but-callable method into a hard panic at runtime (e.g., `env::panic_str("use verify_transaction_inclusion_v2")`), or remove it entirely from the public ABI.
2. If backward compatibility must be preserved, apply the same coinbase-anchor check inside `verify_transaction_inclusion` so both entry points are equally safe.

---

### Proof of Concept

Given a real Bitcoin block `B` at height `H` on the contract's main chain, with Merkle tree:

```
merkle_root = hash(A, C)
A = hash(tx0, tx1)   ← internal node at depth 1, position 0
C = hash(tx2, tx3)   ← internal node at depth 1, position 1
```

Attacker calls (via NEAR RPC, no special role required):

```
verify_transaction_inclusion({
    tx_id:             A,          // internal node hash, not a real tx
    tx_block_blockhash: B,         // real block on main chain
    tx_index:          0,          // position in the forged 2-leaf subtree
    merkle_proof:      [C],        // sibling of A at depth 1
    confirmations:     1,
})
```

`compute_root_from_merkle_proof(A, 0, [C])` = `hash(A, C)` = `merkle_root` → returns `true`.

The function confirms inclusion of a transaction that does not exist. Any contract gating on this result releases funds or advances state based on a forged proof. [5](#0-4) [3](#0-2)

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
