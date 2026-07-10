### Title
`verify_transaction_inclusion_v2` Permanently Blocks Coinbase Verification in Single-Transaction Blocks Due to Inherited v1 Empty-Proof Guard — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` validates a coinbase anchor proof and then delegates entirely to the deprecated `verify_transaction_inclusion`. That inner function contains `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` — a guard designed only for the v1 call path — which also fires when v2 is used to verify a coinbase transaction in a single-transaction block, where an empty `merkle_proof` is correct and expected. The result is a permanent, unconditional panic for a class of valid proofs.

---

### Finding Description

`verify_transaction_inclusion_v2` is the recommended, security-hardened replacement for the deprecated v1 function. It first validates a coinbase anchor proof, then calls the inner v1 function:

```rust
// contract/src/lib.rs  line 367-368
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [1](#0-0) 

The inner `verify_transaction_inclusion` contains this guard:

```rust
// contract/src/lib.rs  line 315
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [2](#0-1) 

This guard was written for the v1 path, where an empty proof is always a caller error. However, it is unconditionally inherited by the v2 path.

**The broken case — single-transaction block:**

In Bitcoin, a block containing only its coinbase transaction has `merkle_root == coinbase_txid`. The correct merkle proof for that coinbase transaction is an empty list (no siblings exist). A caller invoking v2 for this case supplies:

- `merkle_proof = []`
- `coinbase_merkle_proof = []`

The v2 length-equality check passes (`0 == 0`). [3](#0-2) 

The coinbase anchor check passes: `compute_root_from_merkle_proof(coinbase_txid, 0, &[])` iterates zero times and returns `coinbase_txid` unchanged, which equals `header.block_header.merkle_root` for a single-transaction block. [4](#0-3) 

`compute_root_from_merkle_proof` with an empty proof simply returns the input hash: [5](#0-4) 

Control then passes to `verify_transaction_inclusion` with `merkle_proof = []`, and the guard at line 315 **panics unconditionally**, even though the proof is cryptographically valid and complete.

The `ProofArgsV2 → ProofArgs` conversion preserves `merkle_proof` verbatim: [6](#0-5) 

There is no code path in v2 that bypasses or relaxes this check.

---

### Impact Explanation

Any unprivileged NEAR caller — a dApp, bridge, or SPV client — that calls `verify_transaction_inclusion_v2` to verify a coinbase transaction in a single-transaction block receives a contract panic instead of a boolean result. The call always fails; there is no workaround through the v2 API. The v1 function is deprecated and carries the 64-byte forgery vulnerability, so falling back to it is not a safe alternative. Affected dApps cannot verify this class of valid, on-chain transactions, breaking the core SPV guarantee the contract is designed to provide.

---

### Likelihood Explanation

Single-transaction blocks (coinbase only) occur regularly in Bitcoin during low-fee periods and were common in early Bitcoin history. Any integration that needs to verify miner reward transactions or early-chain proofs will encounter this. The trigger requires no privilege, no special role, and no adversarial chain state — only a valid `ProofArgsV2` struct with empty proof vectors, which is the correct input for this block type.

---

### Recommendation

Remove the `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard from `verify_transaction_inclusion`, or add a short-circuit in `verify_transaction_inclusion_v2` before delegating to v1:

```rust
// In verify_transaction_inclusion_v2, before calling v1:
if args.merkle_proof.is_empty() {
    // Single-transaction block: coinbase proof already validated above.
    // tx_id must equal the merkle root directly.
    return args.tx_id == header.block_header.merkle_root;
}
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
```

The guard in v1 was appropriate only when no prior coinbase anchor validation existed. In v2, the coinbase proof already anchors the tree; an empty transaction proof is valid when the transaction IS the coinbase.

---

### Proof of Concept

1. Initialize the contract and submit a block whose only transaction is the coinbase (i.e., `merkle_root == coinbase_txid`).
2. Call `verify_transaction_inclusion_v2` with:
   - `tx_id = coinbase_txid`
   - `tx_block_blockhash = <that block's hash>`
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = coinbase_txid`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
3. **Observed**: contract panics with `"Merkle proof is empty"` at `contract/src/lib.rs:315`.
4. **Expected**: returns `true`, because the coinbase anchor proof passed and `tx_id == merkle_root`.

The root cause is that `verify_transaction_inclusion_v2` reuses `verify_transaction_inclusion` as a shared inner function without removing the v1-only empty-proof guard, mirroring the M-07 pattern where a check appropriate for one operation (entry/deposit / v1 direct call) accidentally blocks a legitimate operation (exit/withdrawal / v2 coinbase verification). [7](#0-6) [8](#0-7)

### Citations

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

**File:** merkle-tools/src/lib.rs (L38-51)
```rust
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
```

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
