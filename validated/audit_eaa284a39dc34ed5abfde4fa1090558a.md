### Title
Deprecated `verify_transaction_inclusion` Remains Callable On-Chain, Enabling 64-Byte Merkle Proof Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is still a live, callable NEAR contract method despite its `#[deprecated]` annotation. It performs no coinbase-proof validation, so any unprivileged NEAR account can supply a crafted `tx_id` that is actually a 64-byte internal Merkle-tree node and receive a `true` return value — falsely asserting that a non-existent Bitcoin transaction is confirmed on-chain. Any downstream contract that gates fund release or cross-chain state on this result is directly exploitable.

---

### Finding Description

The vulnerability class in the reference report is **missing minimum-bound validation on a critical parameter**: `min_dy = 0` lets an attacker control the swap outcome. The direct analog here is **missing proof-integrity validation on `tx_id`**: the deprecated endpoint accepts any 32-byte value as a "transaction hash" with no check that it is a leaf node rather than an internal tree node.

`verify_transaction_inclusion` is decorated with `#[deprecated(since = "0.5.0")]` and `#[pause]`, but in Rust `#[deprecated]` is a compile-time lint only — it does not remove the method from the compiled WASM binary. The function remains a fully reachable NEAR view/call entry point. [1](#0-0) 

The function's only proof check is:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

There is no requirement that `args.tx_id` be a leaf (real transaction). Because Bitcoin's Merkle tree uses the same double-SHA256 hash function at every level, an attacker who knows the tree structure can supply an internal node hash as `tx_id` together with a proof path that reconstructs the correct `merkle_root`. The function returns `true`.

The fixed version, `verify_transaction_inclusion_v2`, closes this gap by first verifying a coinbase proof at index 0 — proving the proof depth is correct — before delegating to the deprecated function: [3](#0-2) 

The project's own internal documentation explicitly acknowledges the attack surface of the deprecated path:

> "This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash." [4](#0-3) 

---

### Impact Explanation

Any NEAR contract (bridge, escrow, cross-chain settlement layer) that calls `verify_transaction_inclusion` and releases funds or updates state on a `true` result can be drained or corrupted. The attacker does not need to mine a Bitcoin block or control a relayer; they only need to read a real block's Merkle tree from the public Bitcoin chain, identify a 64-byte internal node, and submit a crafted `ProofArgs` to the NEAR contract. The forged proof passes all on-chain checks and the function returns `true`.

---

### Likelihood Explanation

- The entry point is fully public — no role, stake, or deposit is required to call `verify_transaction_inclusion`.
- All inputs needed to construct the attack (block Merkle tree structure) are publicly available from any Bitcoin full node or block explorer.
- The NEAR relayer client itself still exposes a `verify_transaction_inclusion` helper that calls the deprecated method, indicating real integrators may still be using it. [5](#0-4) 

Likelihood is **High** given zero privilege requirement and fully public inputs.

---

### Recommendation

1. **Remove `verify_transaction_inclusion` from the compiled contract** by gating it behind a `#[cfg(test)]` attribute or deleting it entirely. A `#[deprecated]` annotation does not prevent on-chain calls.
2. Until removal, add a `require!(false, "use verify_transaction_inclusion_v2")` guard at the top of the function body so any call panics immediately.
3. Audit all downstream NEAR contracts and relayer integrations to ensure they call only `verify_transaction_inclusion_v2`.

---

### Proof of Concept

1. Pick any confirmed Bitcoin block `B` whose Merkle tree has depth ≥ 2. Let the tree be:

   ```
   root = H(H(tx0, tx1), H(tx2, tx3))
   internal_node = H(tx2, tx3)   ← 64 bytes when concatenated before hashing
   ```

2. Craft `ProofArgs`:
   - `tx_id` = `internal_node` (the hash of the right subtree)
   - `tx_block_blockhash` = hash of block `B` (must be on the current main chain)
   - `tx_index` = 1 (right child of root)
   - `merkle_proof` = `[H(tx0, tx1)]` (the left sibling)
   - `confirmations` = 1

3. The contract computes:
   ```
   compute_root_from_merkle_proof(internal_node, 1, [H(tx0,tx1)])
   = H(H(tx0,tx1), internal_node)
   = H(H(tx0,tx1), H(tx2,tx3))
   = root  ✓
   ```

4. `root == header.block_header.merkle_root` → function returns **`true`**, falsely asserting that `internal_node` is a confirmed transaction in block `B`. [6](#0-5)

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

**File:** contract/CLAUDE.md (L64-67)
```markdown
`verify_transaction_inclusion(ProofArgs)` — SPV proof: given a tx hash, block hash, and merkle proof, verifies the transaction is in the block by recomputing the merkle root.

**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.

```

**File:** relayer/src/near_client.rs (L412-437)
```rust
    #[allow(dead_code)]
    pub async fn verify_transaction_inclusion(
        &self,
        transaction_hash: H256,
        transaction_position: usize,
        transaction_block_blockhash: H256,
        merkle_proof: Vec<H256>,
        confirmations: u64,
    ) -> Result<bool, Box<dyn std::error::Error + Send + Sync>> {
        let args = btc_types::contract_args::ProofArgs {
            tx_id: transaction_hash,
            tx_block_blockhash: transaction_block_blockhash,
            tx_index: transaction_position.try_into()?,
            merkle_proof,
            confirmations,
        };

        let tx_hash = self
            .submit_tx(
                self.sign_tx(VERIFY_TRANSACTION_INCLUSION, to_vec(&args)?, 0, None)
                    .await?,
            )
            .await?;
        let response = self.get_tx_status(tx_hash).await?;
        Self::parse_verify_response(response, VERIFY_TRANSACTION_INCLUSION)
    }
```
