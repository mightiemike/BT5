### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the 64-Byte Coinbase Proof Protection Added in v2 — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly accessible on-chain entry point despite being marked `#[deprecated]`. The coinbase merkle proof check introduced in `verify_transaction_inclusion_v2` to mitigate the 64-byte transaction Merkle forgery attack can be completely bypassed by any unprivileged NEAR caller who invokes v1 directly. The result is that the contract can be made to return `true` for a transaction that does not exist, corrupting the SPV proof result that downstream consumers rely on.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to fix a known vulnerability in v1: the 64-byte transaction Merkle proof forgery (second-preimage attack). The fix adds two guards:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` — enforces equal proof depth.
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root` — proves the coinbase (always a real leaf at index 0) is in the block at the same tree depth as the claimed transaction.

Together these guards prevent an attacker from supplying an internal node hash as `tx_id` with a shorter proof, because the coinbase proof would have to be shorter too — but a shorter coinbase proof would not reconstruct the real merkle root. [1](#0-0) 

However, v1 is still `pub` and only gated by `#[pause]`: [2](#0-1) 

In Rust, `#[deprecated]` emits a compiler warning at call sites in Rust source code. It does **not** restrict on-chain access. Any NEAR account can call `verify_transaction_inclusion` directly via a NEAR transaction, completely skipping both coinbase guards.

The underlying merkle verifier `compute_root_from_merkle_proof` in `merkle-tools` is depth-agnostic: it accepts any `transaction_hash` and any-length `merkle_proof` and will reconstruct a root that matches if the inputs are internally consistent — it does not know or check whether the starting hash is a leaf or an internal node. [3](#0-2) 

v1 itself only checks that `merkle_proof` is non-empty and that the computed root matches the stored block header's `merkle_root`: [4](#0-3) 

No coinbase anchor, no depth check.

---

### Impact Explanation

An attacker can make `verify_transaction_inclusion` return `true` for a `tx_id` that is an internal merkle node rather than a real transaction hash. Any downstream NEAR contract (e.g., a bridge, a cross-chain settlement layer, or any consumer of SPV proofs) that calls this function to authorize an action — releasing funds, minting tokens, confirming a deposit — will accept a forged proof and execute the action for a Bitcoin transaction that never occurred.

The corrupted value is the **SPV proof result**: a boolean `true` that asserts transaction inclusion when no such transaction exists on the Bitcoin chain.

---

### Likelihood Explanation

The entry point is fully public and requires no privileged role, no staking, and no special setup beyond knowing a valid mainchain block hash (which is a view-callable public state value). The 64-byte attack technique is well-documented and the required inputs (an internal node hash and a shorter merkle proof) can be computed offline from any Bitcoin block. The attacker only needs to call one NEAR function with crafted arguments.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or gate it with an access-control role so it cannot be called by unprivileged accounts. Since v2 already calls v1 internally via `self.verify_transaction_inclusion(args.into())`, the internal logic can be refactored into a private helper that both the public v2 entry point and the internal call site use, with the public v1 method deleted or made `#[private]`. [5](#0-4) 

---

### Proof of Concept

Consider a confirmed mainchain block `B` with four transactions `[T1, T2, T3, T4]` and the following merkle tree:

```
         root = hash(N1, N2)
        /                   \
  N1 = hash(T1,T2)    N2 = hash(T3,T4)
      /      \              /      \
     T1      T2            T3      T4
```

**Attack steps (all off-chain computation, one on-chain call):**

1. Compute `N1 = double_sha256(T1 || T2)` — this is a real internal node, publicly derivable from the block.
2. Construct a forged `ProofArgs`:
   - `tx_id = N1`
   - `tx_block_blockhash = B`
   - `tx_index = 0`
   - `merkle_proof = [N2]` (one element, one level up)
   - `confirmations = 1`
3. Call `verify_transaction_inclusion(forged_args)` on the NEAR contract.
4. Inside v1, `compute_root_from_merkle_proof(N1, 0, [N2])` computes `hash(N1, N2) = root`, which equals `B.merkle_root`. The function returns `true`.

`verify_transaction_inclusion_v2` would reject this because it would require `coinbase_merkle_proof.len() == 1`, but a valid coinbase proof for a 4-tx tree has length 2 — so the coinbase proof at depth 1 would not reconstruct the real root. The v1 bypass sidesteps this entirely. [6](#0-5) [3](#0-2)

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
