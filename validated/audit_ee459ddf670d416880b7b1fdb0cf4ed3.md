### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Transaction Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function is still a live, unpermissioned public entry point on the NEAR contract. It lacks the coinbase-proof depth-equality guard that `verify_transaction_inclusion_v2` uses to defeat the 64-byte Merkle-node forgery attack. Any unprivileged NEAR caller — including a recipient contract consuming SPV results — can invoke it directly and receive a fraudulent `true` return for a Bitcoin transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` is annotated `#[deprecated]` and carries an explicit warning comment, but Rust's `#[deprecated]` attribute is a compile-time lint only; it does not restrict runtime call-ability. The function remains `pub`, carries only the `#[pause]` gate (which is inactive in normal operation), and is reachable by any NEAR account. [1](#0-0) 

The function's sole Merkle check is:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

`compute_root_from_merkle_proof` is a pure positional hash-chain walk; it does not distinguish leaf nodes from internal nodes. [3](#0-2) 

The only guard against an empty proof is:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [4](#0-3) 

There is no check that `tx_id` is a leaf-level hash. An attacker who supplies an internal-node hash `N` together with a sibling path that reconstructs the real Merkle root will receive `true`.

`verify_transaction_inclusion_v2` defeats this by requiring `merkle_proof.len() == coinbase_merkle_proof.len()` and independently anchoring the coinbase leaf at position 0. Because the coinbase proof depth equals the full tree depth, a proof rooted at any internal node (which would be shorter) cannot satisfy the length-equality constraint. [5](#0-4) 

The v1 function has none of these guards.

---

### Impact Explanation

A recipient NEAR contract that calls `verify_transaction_inclusion` to gate a cross-chain action (token release, bridge settlement, oracle update) can be made to accept a proof for a Bitcoin transaction that was never broadcast or confirmed. The corrupted value is the SPV proof result: the function returns `true` for a fabricated inclusion claim. This directly enables proof-verification forgery against any consumer of the v1 API.

---

### Likelihood Explanation

The function is publicly callable without any role restriction when the contract is unpaused. The 64-byte transaction forgery technique is publicly documented (BitMEX research, referenced in the contract's own v2 docstring). Constructing a valid internal-node proof requires only knowledge of the target block's Merkle tree, which is public Bitcoin data. No privileged access, key material, or social engineering is required.

---

### Recommendation

Make `verify_transaction_inclusion` private (remove `pub`) so it is only reachable internally by `verify_transaction_inclusion_v2`. The v2 function already calls it via `#[allow(deprecated)] self.verify_transaction_inclusion(args.into())`, so internal access is sufficient. Alternatively, inline the logic into v2 and delete v1 entirely. Do not rely solely on the `#[deprecated]` lint to discourage use; it has no runtime effect.

---

### Proof of Concept

Given a confirmed Bitcoin block with Merkle tree:

```
        root
       /    \
     N1      N2
    /  \    /  \
   T1  T2  T3  T4
```

1. Observe `N1 = hash(T1, T2)` and `N2 = hash(T3, T4)` from public block data.
2. Call `verify_transaction_inclusion` with:
   - `tx_id = N1` (an internal node, not a real transaction)
   - `tx_index = 0`
   - `merkle_proof = [N2]`
   - `tx_block_blockhash` = hash of the real block
   - `confirmations = 1`
3. `compute_root_from_merkle_proof(N1, 0, [N2])` computes `hash(N1, N2) = root`, which equals `header.block_header.merkle_root`.
4. The function returns `true` — a forged SPV proof accepted for a non-existent transaction. [3](#0-2) [6](#0-5)

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
