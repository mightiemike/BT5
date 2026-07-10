### Title
`verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase Merkle Proof Check Enforced by `verify_transaction_inclusion_v2` - (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte transaction Merkle proof forgery vulnerability by requiring a valid coinbase Merkle proof alongside the transaction proof. However, the original `verify_transaction_inclusion` function remains a live, publicly callable NEAR entry point with no access control beyond `#[pause]`. Any unprivileged NEAR caller can invoke it directly, bypassing the coinbase proof check entirely and exploiting the known forgery vulnerability.

### Finding Description

`verify_transaction_inclusion_v2` enforces a coinbase Merkle proof check before delegating to `verify_transaction_inclusion`:

```rust
// contract/src/lib.rs:346-368
#[pause]
pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
    require!(
        args.merkle_proof.len() == args.coinbase_merkle_proof.len(), ...
    );
    let header = self.headers_pool.get(&args.tx_block_blockhash)...;
    require!(
        merkle_tools::compute_root_from_merkle_proof(
            args.coinbase_tx_id.clone(), 0usize, &args.coinbase_merkle_proof,
        ) == header.block_header.merkle_root,
        "Incorrect coinbase merkle proof"
    );
    #[allow(deprecated)]
    self.verify_transaction_inclusion(args.into())
}
```

The deprecated `verify_transaction_inclusion` is still a `pub` NEAR method with only `#[pause]` on it — no role guard, no access restriction:

```rust
// contract/src/lib.rs:283-323
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
    // ... no coinbase proof check ...
    merkle_tools::compute_root_from_merkle_proof(
        args.tx_id, usize::try_from(args.tx_index).unwrap(), &args.merkle_proof,
    ) == header.block_header.merkle_root
}
```

The contract's own doc comment acknowledges the danger:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."

The `ProofArgs` struct accepted by the old function requires no coinbase fields:

```rust
// btc-types/src/contract_args.rs:16-24
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

Any NEAR caller can submit a `ProofArgs` that exploits the 64-byte internal-node forgery without ever touching `verify_transaction_inclusion_v2`.

### Impact Explanation

**Impact: Medium**

The 64-byte transaction Merkle proof forgery (https://www.bitmex.com/blog/64-Byte-Transactions) allows an attacker to craft a fake 64-byte "transaction" that is actually an internal Merkle tree node. By supplying a crafted Merkle proof for this fake `tx_id`, `verify_transaction_inclusion` returns `true` for a Bitcoin transaction that was never broadcast or confirmed. Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate an action (e.g., releasing funds upon proof of a Bitcoin payment) can be deceived into accepting a forged proof. The security upgrade introduced by `verify_transaction_inclusion_v2` is rendered optional rather than mandatory, because the insecure path is always reachable.

### Likelihood Explanation

**Likelihood: Medium**

The entry point is fully open to any NEAR account — no staking, no role, no deposit beyond gas is required. The 64-byte forgery technique is publicly documented and well understood. The only prerequisite is knowledge of a real confirmed Bitcoin block hash already stored in the contract's `headers_pool`, which is public on-chain state. Integrators who read the deprecation notice and migrate to v2 are still vulnerable if the contract they call exposes the old method.

### Recommendation

Remove `verify_transaction_inclusion` as a public NEAR entry point. The simplest fix is to drop the `pub` visibility and the `#[near]`/`#[pause]` attributes so it becomes a private helper callable only from `verify_transaction_inclusion_v2`:

```diff
-    #[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
-    #[pause]
-    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
+    fn verify_transaction_inclusion_inner(&self, args: ProofArgs) -> bool {
```

And update the call site in `verify_transaction_inclusion_v2` accordingly. This ensures the coinbase proof check is the only path to Merkle proof verification.

### Proof of Concept

1. Identify any block hash `B` stored in the contract's `mainchain_header_to_height` map (public view call).
2. Construct a 64-byte value `N` that is a valid internal Merkle tree node within block `B`'s Merkle tree.
3. Build a `ProofArgs` with `tx_id = N`, `tx_block_blockhash = B`, and a Merkle proof path that reconstructs the Merkle root from `N`.
4. Call `verify_transaction_inclusion` directly (not `_v2`) with this `ProofArgs`.
5. The function computes `compute_root_from_merkle_proof(N, index, proof)` and compares it to the stored `merkle_root`. Because `N` is a genuine internal node, the root matches and the function returns `true` — confirming a Bitcoin transaction that does not exist.

---

**Root cause location:** [1](#0-0) 

**Secure entry point that enforces the coinbase check:** [2](#0-1) 

**`ProofArgs` struct (no coinbase fields, accepted by the insecure path):** [3](#0-2)

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
