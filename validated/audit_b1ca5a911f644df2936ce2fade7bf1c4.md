### Title
Caller-Controlled `confirmations` Parameter Bypasses Finality Guarantee in SPV Proof Verification — (`contract/src/lib.rs`)

### Summary

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `confirmations: u64` field with no minimum floor. Any unprivileged NEAR account can pass `confirmations = 0`, causing the contract to return `true` for a transaction that has zero depth below the chain tip. This is the direct analog of the `gulp` slippage attack: a user-controlled security threshold with no lower-bound enforcement, callable by anyone, that silently degrades the security guarantee the function is supposed to provide.

### Finding Description

`ProofArgs` and `ProofArgsV2` both carry a `confirmations: u64` field that is passed verbatim from the caller. [1](#0-0) [2](#0-1) 

Inside `verify_transaction_inclusion`, the only bound check on `confirmations` is an **upper** cap against `gc_threshold`; there is no lower-bound check: [3](#0-2) 

The confirmation-depth check that follows is: [4](#0-3) 

When `args.confirmations == 0`, the right-hand side of `>=` is `0`. Because all operands are `u64`, the expression `(height_diff + 1) >= 0` is trivially true for every possible block, so the guard never fires. The function then proceeds to Merkle-proof verification and returns `true` for a transaction that sits at the very tip of the chain — or even in a block submitted just seconds ago — with no reorg protection whatsoever.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase-proof check, so it inherits the same flaw: [5](#0-4) 

Neither function carries any access-control modifier beyond `#[pause]`; they are reachable by any unprivileged NEAR caller.

### Impact Explanation

The `confirmations` parameter is the sole mechanism by which the contract enforces Bitcoin finality for downstream consumers. A dApp that calls `verify_transaction_inclusion_v2` to gate a cross-chain asset release (bridge, DEX settlement, lending collateral unlock) relies on the contract to enforce the required depth. Because the contract imposes no minimum, an attacker can:

1. Broadcast a BTC transaction and wait for it to appear in a single block.
2. Have a relayer (or submit directly) that block to the light client.
3. Immediately call `verify_transaction_inclusion_v2` with `confirmations = 0`.
4. Receive `true` and trigger the downstream dApp's fund-release logic.
5. Simultaneously attempt a double-spend or exploit a shallow reorg to reverse the BTC transaction.

The corrupted value is the **proof result** (`true`) returned for a transaction that has not achieved the finality depth the consuming protocol requires. This maps directly to the broken invariant: the contract's API contract promises that `confirmations` blocks have built on top of the transaction's block, but the contract never enforces a floor.

### Likelihood Explanation

The entry path requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion_v2` with an arbitrary `ProofArgsV2` struct. The only prerequisite is that the target transaction's block has been submitted to the light client, which is the normal operating condition. Likelihood is **high** for any deployment where a dApp trusts the contract to enforce finality rather than hardcoding its own minimum in the calling contract.

### Recommendation

Enforce a protocol-level minimum confirmation floor inside the contract. Two complementary fixes:

1. **Add a minimum constant** (e.g., `MIN_CONFIRMATIONS = 6` for Bitcoin mainnet) and `require!(args.confirmations >= MIN_CONFIRMATIONS, "confirmations below minimum")` at the top of `verify_transaction_inclusion`.
2. **Expose the minimum as a contract parameter** set at `init` time (alongside `gc_threshold`) so it can be tuned per chain (Bitcoin vs. Litecoin vs. Dogecoin have different reorg risk profiles) without a code upgrade.

This mirrors the oracle-based slippage floor recommended in the original report: the contract, not the caller, must own the security threshold.

### Proof of Concept

```
# 1. Submit a single BTC block header to the light client (normal relayer operation).
#    The block contains tx T at index 0.

near call <contract> submit_blocks '{"headers": [<block_bytes>]}' ...

# 2. Immediately call verify_transaction_inclusion_v2 with confirmations=0.
#    merkle_proof and coinbase_merkle_proof are valid proofs for block B.

near call <contract> verify_transaction_inclusion_v2 '{
  "args": {
    "tx_id":                "<T_hash>",
    "tx_block_blockhash":   "<B_hash>",
    "tx_index":             0,
    "merkle_proof":         [...],
    "coinbase_tx_id":       "<coinbase_hash>",
    "coinbase_merkle_proof":[...],
    "confirmations":        0        # <-- attacker-controlled, no floor enforced
  }
}' ...

# Result: true
# Block B is at the chain tip; zero additional blocks have built on it.
# The contract returns true because (tip_height - B_height + 1) = 1 >= 0.
# Any dApp consuming this result will treat T as finalized.
```

The `confirmations = 0` value passes the only guard (`0 <= gc_threshold`) and trivially satisfies the depth check, returning `true` with no reorg protection. [6](#0-5)

### Citations

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

**File:** btc-types/src/contract_args.rs (L26-36)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
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
