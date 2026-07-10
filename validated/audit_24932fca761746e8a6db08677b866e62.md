### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Transaction Forgery Protection Introduced in v2 — (File: `contract/src/lib.rs`)

---

### Summary

The contract exposes two transaction-inclusion verification entry points. `verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle-proof forgery vulnerability by requiring a coinbase merkle proof. However, the original `verify_transaction_inclusion` (v1) remains a live, unpermissioned public method. Any NEAR caller can invoke v1 directly, bypassing the coinbase validation entirely and obtaining a `true` result for a transaction that never existed in the Bitcoin blockchain.

---

### Finding Description

`verify_transaction_inclusion_v2` enforces a two-step check:

1. Validate that the coinbase transaction's merkle proof matches the block's `merkle_root`.
2. Delegate to `verify_transaction_inclusion` (v1) for the actual transaction proof. [1](#0-0) 

The coinbase check exists solely to prevent the well-known 64-byte internal-node forgery: because every internal merkle-tree node is itself a 32-byte hash, an attacker can present an internal node as a `tx_id` and supply a valid sibling path that reconstructs the correct `merkle_root`. The coinbase proof anchors the tree structure and defeats this attack.

v1 performs no such anchor check: [2](#0-1) 

The `#[deprecated]` Rust attribute only emits a compiler warning for Rust callers. It has no effect on external NEAR callers invoking the method via a transaction or cross-contract call. The method carries only `#[pause]`, meaning it is active by default and callable by any unprivileged account. [3](#0-2) 

The structural parallel to the reported Phi vulnerability is exact:

| Phi (`PhiFactory`) | This contract |
|---|---|
| `Claimable::signatureClaim` substitutes `block.chainid` before forwarding to `PhiFactory::signatureClaim` | `verify_transaction_inclusion_v2` validates the coinbase proof before forwarding to `verify_transaction_inclusion` |
| `PhiFactory::signatureClaim` is callable directly, skipping the chain-id substitution | `verify_transaction_inclusion` is callable directly, skipping the coinbase validation |
| Result: cross-chain signature replay | Result: 64-byte internal-node proof forgery |

The `ProofArgs` struct accepted by v1 contains no coinbase fields at all: [4](#0-3) 

There is no way to retrofit the coinbase check into v1 without changing its signature; the protection is structurally absent.

---

### Impact Explanation

`verify_transaction_inclusion` is the contract's public API for SPV proof verification. Consumer contracts and applications that call it to authorize security-sensitive actions — releasing bridged funds, minting wrapped tokens, confirming cross-chain settlements — will receive `true` for a transaction that was never broadcast or mined. An attacker can fabricate proof of any Bitcoin transaction they choose, provided they know the merkle root of any block already accepted by the light client (all of which are public on-chain state).

The corrupted value is the boolean proof result returned to the caller: a `false` (no inclusion) is flipped to `true` (inclusion confirmed) without any valid Bitcoin transaction existing.

---

### Likelihood Explanation

The attack requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion` directly. The 64-byte internal-node technique is publicly documented (linked in the contract's own comments at line 268) and requires only knowledge of a block's merkle tree, which is freely available from any Bitcoin block explorer. The attacker needs to:

1. Pick any block already in the light client's `headers_pool`.
2. Compute an internal merkle-tree node from that block's public transaction list.
3. Construct the sibling path for that node.
4. Call `verify_transaction_inclusion` with the internal node as `tx_id`. [5](#0-4) 

---

### Recommendation

Remove `verify_transaction_inclusion` from the public interface entirely. Because `verify_transaction_inclusion_v2` already delegates to it internally, no external caller has a legitimate reason to bypass the coinbase check. The simplest fix is to change `verify_transaction_inclusion` from a `pub` method to a `pub(crate)` or private helper, making it unreachable from outside the contract while preserving the internal delegation path used by v2. [6](#0-5) 

---

### Proof of Concept

```
1. Let B be any block hash present in the light client's mainchain
   (readable via get_block_hash_by_height or get_last_block_header).

2. Fetch block B's full transaction list from a public Bitcoin node.
   Compute the merkle tree. Pick any internal node N at depth d,
   position p (N is a 32-byte double-SHA256 hash — identical in
   format to a transaction hash).

3. Build the sibling path from N up to the merkle root (length = d).
   This is a valid merkle_proof for "transaction" N at index p.

4. Call verify_transaction_inclusion with:
     tx_id             = N          (internal node, not a real tx)
     tx_block_blockhash = B
     tx_index          = p
     merkle_proof      = sibling path from step 3
     confirmations     = 1

5. The function computes:
     compute_root_from_merkle_proof(N, p, sibling_path)
     == block B's merkle_root   ✓

   and returns true — confirming inclusion of a transaction
   that does not exist.

6. Any consumer contract that trusted this result to release
   funds or mint tokens is now exploited.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L263-323)
```rust
    /// Verifies that a transaction is included in a block at a given block height
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
    ///
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
