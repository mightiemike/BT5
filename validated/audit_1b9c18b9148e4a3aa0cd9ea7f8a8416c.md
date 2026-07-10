I have all the information needed. Let me trace the exact math to confirm the attack.

**5-tx tree walkthrough:**

Tree: `[T0, T1, T2, T3, T4]` → padded to `[T0, T1, T2, T3, T4, T4]`

Level 1: `[H(T0,T1)=A, H(T2,T3)=B, H(T4,T4)=C]` → padded to `[A, B, C, C]`
Level 2: `[H(A,B)=D, H(C,C)=E]`
Root: `H(D,E)`

**Proof for T4 at index 4** = `[T4, C, D]`

**Verify at index 4 (real):**
- pos=4 (even): `H(T4, T4)=C`, pos→2
- pos=2 (even): `H(C, C)=E`, pos→1
- pos=1 (odd): `H(D, E)=Root` ✓

**Verify same proof at index 5 (phantom):**
- pos=5 (odd): `H(proof[0]=T4, T4)=C`, pos→2
- pos=2 (even): `H(C, proof[1]=C)=E`, pos→1
- pos=1 (odd): `H(proof[2]=D, E)=Root` ✓

The same proof verifies at both index 4 (real) and index 5 (nonexistent). This is confirmed by the code.

---

### Title
Odd-Leaf Duplicate Position Reuse Allows Same Merkle Proof to Verify Two Distinct `tx_index` Values — (`merkle-tools/src/lib.rs::compute_root_from_merkle_proof`)

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position` against the actual tree width. For any odd-width Bitcoin Merkle tree with `N` leaves, the proof generated for the real last leaf at index `N-1` also verifies correctly when submitted with index `N` (the phantom duplicate position). `verify_transaction_inclusion` passes the caller-supplied `tx_index` directly to this function without any validation, so an unprivileged caller can obtain a `true` return for a `(tx_id, block_hash, tx_index=N)` tuple that refers to a position that does not exist in the block.

### Finding Description

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` iterates over proof elements and at each step uses `current_position % 2` to decide left/right placement, then divides by 2. [1](#0-0) 

There is no check that `transaction_position < tree_leaf_count`. Bitcoin block headers do not encode the transaction count, so the contract cannot derive the bound from on-chain data. [2](#0-1) 

When the tree has an odd number of leaves `N`, Bitcoin's construction duplicates the last leaf before hashing each level. The proof for leaf `N-1` therefore contains `leaf[N-1]` as its first sibling. When the same proof is replayed with `tx_index = N`:

- Step 1: position `N` is odd → `H(proof[0]=leaf[N-1], leaf[N-1])` — identical to the real step 1 result.
- All subsequent steps are identical because `N/2 == (N-1)/2` for odd `N`.

The final computed root equals the block's stored `merkle_root`, so `verify_transaction_inclusion` returns `true`. [3](#0-2) 

`verify_transaction_inclusion_v2` does not fix this: it validates only that the coinbase proof is correct (at fixed index 0), then delegates to the deprecated v1 function with the attacker-supplied `tx_index` unchanged. [4](#0-3) 

### Impact Explanation

Any downstream bridge, mint, or withdrawal contract that:
1. calls `verify_transaction_inclusion` (or v2) to gate an economic action, and
2. uses `(tx_id, block_hash, tx_index)` as its replay-protection key

can be double-spent. The attacker first redeems the real event at index `N-1`, then replays the same transaction hash with index `N`. Both calls return `true` from the light client. The bridge sees two distinct keys and processes the event twice, enabling double-minting or double-withdrawal.

The GC timing angle in the question title is a red herring: the `mainchain_header_to_height` lookup at line 300 already rejects GC'd blocks, so GC timing does not open or close this path. [5](#0-4) 

### Likelihood Explanation

- The function is public and callable by any NEAR account with no role restriction (only the `#[pause]` gate, which is off in normal operation).
- Any Bitcoin block with an odd transaction count (the majority of mainnet blocks) is a valid target.
- The attacker needs only the real Merkle proof for the last transaction, which is publicly derivable from the Bitcoin blockchain.
- No privileged key, relayer compromise, or social engineering is required.

### Recommendation

1. **Enforce proof-depth binding via coinbase proof length.** In `verify_transaction_inclusion_v2`, require that `tx_index < 2^(coinbase_merkle_proof.len())`. The coinbase proof length equals `ceil(log2(tx_count))`, so `2^depth` is an upper bound on the tree width. This is already available in v2 without storing the tx count.
2. **Reject `tx_index >= 2^proof_depth`** inside `compute_root_from_merkle_proof` or at the call site in `verify_transaction_inclusion`.
3. Deprecate and disable `verify_transaction_inclusion` (v1) entirely; it lacks the coinbase proof needed to apply the above bound.

### Proof of Concept

```rust
// 5-tx odd tree: T0..T4
// proof_for_T4 = merkle_proof_calculator([T0,T1,T2,T3,T4], 4)
//              = [T4, H(T4,T4), H(H(T0,T1),H(T2,T3))]

let root_at_4 = compute_root_from_merkle_proof(T4, 4, &proof_for_T4);
let root_at_5 = compute_root_from_merkle_proof(T4, 5, &proof_for_T4);
assert_eq!(root_at_4, root_at_5); // passes — same root, phantom index
```

Both calls return the block's real `merkle_root`. Submitting `(tx_id=T4, tx_index=5, proof=proof_for_T4)` to `verify_transaction_inclusion` against a block that contains exactly 5 transactions returns `true`, even though index 5 does not exist. [1](#0-0) [6](#0-5)

### Citations

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

**File:** contract/CLAUDE.md (L64-67)
```markdown
`verify_transaction_inclusion(ProofArgs)` — SPV proof: given a tx hash, block hash, and merkle proof, verifies the transaction is in the block by recomputing the merkle root.

**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.

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
