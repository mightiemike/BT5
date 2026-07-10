### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Merkle Forgery Protection — (`contract/src/lib.rs`)

---

### Summary

The contract exposes two transaction-inclusion verification endpoints. `verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase proof. However, the original `verify_transaction_inclusion` function remains a fully public, unpermissioned NEAR call. Any unprivileged caller can invoke it directly, bypassing the coinbase proof check entirely and obtaining a `true` result for a fabricated transaction.

---

### Finding Description

`verify_transaction_inclusion` is marked `#[deprecated]` at the Rust compiler level but is still compiled into the WASM binary as a public contract method decorated only with `#[pause]` — no role restriction, no access control. [1](#0-0) 

The `#[deprecated]` attribute is a Rust lint hint; it has no effect on the compiled NEAR ABI. The method remains callable by any NEAR account.

`verify_transaction_inclusion_v2` was introduced to close the known 64-byte forgery attack by first verifying a coinbase Merkle proof before delegating to the v1 logic: [2](#0-1) 

The coinbase check at lines 358–365 is the only guard against the forgery. Because v1 is still reachable directly, that guard is entirely optional from the caller's perspective.

The v1 function itself only checks that the supplied `merkle_proof` path reconstructs to the block's stored `merkle_root`: [3](#0-2) 

The `compute_root_from_merkle_proof` function in `merkle-tools` performs a straightforward iterative hash-and-combine with no structural validation: [4](#0-3) 

---

### Impact Explanation

The 64-byte transaction forgery attack (documented at https://www.bitmex.com/blog/64-Byte-Transactions) allows an attacker to craft a 64-byte blob whose double-SHA256 hash equals an internal Merkle tree node. By supplying this blob as `tx_id` with a crafted `merkle_proof`, the attacker can make `verify_transaction_inclusion` return `true` for a Bitcoin transaction that was never mined.

Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate a cross-chain action — releasing locked funds, minting wrapped tokens, or crediting a balance — will accept the forged proof and execute the action. The `ProofArgs` struct accepts fully attacker-controlled fields: [5](#0-4) 

The structural analog to the reported burn bug is exact: just as `burn(account, amount)` allowed any caller to destroy any account's tokens by omitting a `msg.sender` check, `verify_transaction_inclusion` allows any caller to forge any transaction proof by omitting the coinbase check that `verify_transaction_inclusion_v2` enforces.

---

### Likelihood Explanation

- **Entry path**: Any unprivileged NEAR account. No staking, no role, no deposit required beyond gas.
- **Knowledge required**: The 64-byte forgery technique is publicly documented and tooled.
- **Precondition**: The target block must already be in the light client's `headers_pool` — a normal operational state, not an edge case.
- **Detection difficulty**: The call looks identical to a legitimate v1 verification call; no on-chain event distinguishes it.

Likelihood is **High**.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public NEAR ABI entirely. Because `verify_transaction_inclusion_v2` already delegates to it internally via `#[allow(deprecated)] self.verify_transaction_inclusion(args.into())`, the v1 logic can be refactored into a private helper function:

```rust
// Make private — no longer a public contract method
fn verify_transaction_inclusion_inner(&self, args: ProofArgs) -> bool { ... }
```

`verify_transaction_inclusion_v2` then calls the private helper. This eliminates the bypass path while preserving all existing internal call sites. [6](#0-5) 

---

### Proof of Concept

1. Identify a real Bitcoin block `B` already stored in the light client (any block in `headers_pool` with sufficient confirmations).
2. Obtain `B`'s Merkle root `R` and its transaction list.
3. Compute an internal Merkle node `N` at depth 1: `N = dSHA256(tx0 || tx1)`.
4. Craft a 64-byte payload `P` such that `dSHA256(P) == N` (the forgery construction from the BitMEX post).
5. Build a `merkle_proof` path from `N` upward to `R` using the real sibling hashes.
6. Call `verify_transaction_inclusion` directly (not v2) with:
   - `tx_id = dSHA256(P)` (the forged "transaction hash")
   - `tx_block_blockhash = B`
   - `tx_index = 0`
   - `merkle_proof = [sibling of N, ...]`
   - `confirmations = 1`
7. The function returns `true`. No coinbase proof was ever checked. [7](#0-6)

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
