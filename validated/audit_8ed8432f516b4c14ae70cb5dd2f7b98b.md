### Title
Zero `confirmations` value trivially bypasses confirmation-depth check in `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `confirmations: u64` field with no minimum enforcement. When a caller passes `confirmations = 0`, the confirmation-depth guard reduces to `X >= 0` — always true for `u64` — so the check is silently bypassed. Any unprivileged NEAR account can call either function with `confirmations = 0` and receive a `true` result for a transaction that sits at the chain tip with zero burial depth, completely defeating the SPV security guarantee the contract is designed to provide.

---

### Finding Description

`ProofArgs` and `ProofArgsV2` both declare `confirmations` as a plain `u64` with no lower-bound constraint: [1](#0-0) [2](#0-1) 

Inside `verify_transaction_inclusion`, the only guard on confirmation depth is:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [3](#0-2) 

When `args.confirmations == 0`, the left-hand side is some `u64 ≥ 0`, so the `require!` never fires regardless of how recently the block was submitted. The transaction block can be the current chain tip — zero blocks deep — and the function still returns the Merkle-root comparison result as if the transaction were fully confirmed.

`verify_transaction_inclusion_v2` delegates directly to `verify_transaction_inclusion` after its coinbase-proof check, so it inherits the same flaw: [4](#0-3) 

There is also a secondary guard:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    ...
);
``` [5](#0-4) 

This only prevents `confirmations` from exceeding the GC window; it does not prevent `confirmations = 0`.

---

### Impact Explanation

The confirmation depth is the sole on-chain mechanism that ensures a verified transaction is buried deeply enough to survive a Bitcoin chain reorganization. Bypassing it with `confirmations = 0` means:

- A downstream contract that calls `verify_transaction_inclusion_v2` with `confirmations = 0` will receive `true` for a transaction that is in the most recently submitted block — a block that could still be reorganized away.
- An attacker who controls a downstream contract (or who can influence what `confirmations` value a victim contract passes) can trigger asset release or state changes on NEAR based on a Bitcoin transaction that is subsequently erased by a reorg.
- The corrupted value is the boolean proof result returned to the caller: it asserts "confirmed" when the transaction has zero confirmation depth.

---

### Likelihood Explanation

The entry path is fully unprivileged: `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` carry no `#[trusted_relayer]` or role guard — any NEAR account can call them. The `confirmations` field is a plain integer in a Borsh-serialized struct; passing `0` requires no special knowledge or capability. The existing test suite already exercises `confirmations: 0` as a routine input: [6](#0-5) 

confirming the path is reachable and accepted by the contract today.

---

### Recommendation

Enforce a minimum confirmation depth inside `verify_transaction_inclusion`:

```rust
require!(args.confirmations >= 1, "confirmations must be at least 1");
```

Alternatively, expose a contract-level `min_confirmations` configuration (analogous to `gc_threshold`) that is validated at init time and checked against every proof request, so the protocol can enforce a chain-specific safe minimum (e.g., 6 for Bitcoin mainnet) rather than leaving the choice entirely to callers.

---

### Proof of Concept

1. Deploy the contract with a valid Bitcoin genesis block and `skip_pow_verification = false`.
2. Have the trusted relayer submit one new block containing a target transaction `tx_id`.
3. As any unprivileged NEAR account, call:
   ```json
   verify_transaction_inclusion_v2({
     "tx_id": "<tx_id>",
     "tx_block_blockhash": "<tip_block_hash>",
     "tx_index": 0,
     "merkle_proof": [...],
     "coinbase_tx_id": "<coinbase_hash>",
     "coinbase_merkle_proof": [...],
     "confirmations": 0
   })
   ```
4. The call returns `true`. The transaction block is the current chain tip — `heaviest_block_height - target_block_height + 1 = 1`, but `1 >= 0` trivially passes.
5. A downstream contract that gates asset release on this boolean now releases assets for a transaction with zero confirmation depth, which a miner with sufficient hash power could reorganize away.

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

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L303-308)
```rust
        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );
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

**File:** contract/tests/test_basics.rs (L383-390)
```rust
            .args_borsh(ProofArgs {
                tx_id: merkle_tools::H256::default(),
                tx_block_blockhash: genesis_block_header().block_hash(),
                tx_index: 0,
                merkle_proof: vec![merkle_tools::H256::default()],
                confirmations: 0,
            })
            .await?
```
