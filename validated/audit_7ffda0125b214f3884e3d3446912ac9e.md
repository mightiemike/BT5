### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable Without Coinbase Proof Guard, Enabling 64-Byte Merkle Proof Forgery - (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is still a live, publicly callable NEAR contract method despite being deprecated. It lacks the coinbase Merkle proof validation that was specifically introduced in `verify_transaction_inclusion_v2` to close the 64-byte transaction Merkle proof forgery vulnerability. Any unprivileged NEAR caller can invoke the old entry point directly, bypassing the guard entirely, and obtain a `true` return value for a forged transaction inclusion proof.

---

### Finding Description

The contract exposes two proof-verification entry points:

**`verify_transaction_inclusion` (deprecated, still public):** [1](#0-0) 

This function accepts a `ProofArgs` struct containing `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, and `confirmations`. It verifies only that `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.block_header.merkle_root`. There is no check that `tx_id` is a real leaf-level transaction rather than an internal Merkle node. [2](#0-1) 

**`verify_transaction_inclusion_v2` (current, guarded):** [3](#0-2) 

This version adds a mandatory coinbase proof check before delegating to the deprecated function: [4](#0-3) 

The coinbase proof check is the security guard. By requiring a valid proof that the coinbase transaction (always at index 0) hashes to the block's `merkle_root`, the function ensures the Merkle tree is consistent with a real Bitcoin block, making it computationally infeasible to simultaneously forge both the coinbase proof and the target transaction proof.

**The problem:** In NEAR, a Rust `#[deprecated]` attribute only emits a compiler warning for Rust callers. It does **not** remove the method from the on-chain ABI. Any NEAR account — including an attacker — can call `verify_transaction_inclusion` directly via a NEAR transaction, completely bypassing the coinbase guard in `verify_transaction_inclusion_v2`.

The `ProofArgs` struct accepted by the deprecated function: [5](#0-4) 

contains no `coinbase_tx_id` or `coinbase_merkle_proof` fields, so the guard cannot be applied even if the caller wanted to.

The `compute_root_from_merkle_proof` function in `merkle-tools`: [6](#0-5) 

performs no validation that `transaction_hash` is a leaf node. It blindly hashes whatever is provided as the starting value.

---

### Impact Explanation

The 64-byte transaction Merkle proof forgery attack (documented at https://www.bitmex.com/blog/64-Byte-Transactions, referenced in the contract's own deprecation notice) works as follows:

1. A Bitcoin Merkle internal node is the double-SHA256 of two concatenated 32-byte child hashes — exactly 64 bytes of input.
2. A valid Bitcoin transaction can also be 64 bytes.
3. An attacker identifies a real Bitcoin block and its Merkle root.
4. The attacker crafts a 64-byte value that, when treated as a `tx_id` leaf and combined with a chosen `merkle_proof`, produces the block's real `merkle_root`.
5. The attacker calls `verify_transaction_inclusion` directly with this crafted `tx_id`, a valid `tx_block_blockhash` (any confirmed block), and the crafted proof.
6. The function returns `true`.

The broken invariant is: **a `true` return from `verify_transaction_inclusion` no longer guarantees that `tx_id` is a real Bitcoin transaction included in the specified block.** It may be an internal Merkle node that was never broadcast as a transaction.

Any downstream NEAR contract (bridge, atomic swap, cross-chain lending protocol) that gates fund releases on a `true` result from `verify_transaction_inclusion` can be drained by an attacker who fabricates a Bitcoin transaction inclusion proof for a non-existent payment.

---

### Likelihood Explanation

- The entry point is publicly callable by any NEAR account with no role or stake requirement.
- The 64-byte attack is well-documented and has known construction techniques.
- The Bitcoin Merkle tree structure of any block is public information, giving the attacker all inputs needed to craft the forgery.
- The contract's own deprecation notice explicitly names this attack vector, confirming the developers are aware the old function is unsafe — yet it remains live.
- The `#[pause]` attribute on the function provides no protection unless a `PauseManager` actively pauses it; it is unpaused by default. [7](#0-6) 

---

### Recommendation

Remove `verify_transaction_inclusion` from the public NEAR ABI entirely. In NEAR/Rust, this means either:

1. **Make it `pub(crate)` or private** — removing it from the `#[near]` impl block so it is no longer exposed as a callable contract method.
2. **Add the coinbase proof check directly** to `verify_transaction_inclusion` so both entry points are equally safe, then remove the deprecated one.

Simply marking it `#[deprecated]` is insufficient because the Rust deprecation attribute has no effect on the on-chain ABI.

---

### Proof of Concept

1. Deploy the contract (Bitcoin feature flag) with a known mainnet genesis block.
2. Wait for a confirmed block at height H with Merkle root `R`.
3. Construct a 64-byte value `fake_tx` such that `double_sha256(double_sha256(fake_tx || sibling) || ...)` equals `R` for some chosen `sibling` values (standard 64-byte attack construction).
4. Call `verify_transaction_inclusion` directly on the NEAR contract:
   ```json
   {
     "tx_id": "<fake_tx as H256>",
     "tx_block_blockhash": "<hash of block at height H>",
     "tx_index": <chosen index>,
     "merkle_proof": ["<sibling hashes>"],
     "confirmations": 1
   }
   ```
5. The function returns `true` despite `fake_tx` never existing as a Bitcoin transaction.
6. A bridge contract that calls `verify_transaction_inclusion` to authorize a withdrawal now releases funds for a payment that never occurred on Bitcoin.

The same call to `verify_transaction_inclusion_v2` would fail at the coinbase proof check: [4](#0-3) 

because the attacker cannot simultaneously forge a valid coinbase proof for the same Merkle root.

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
