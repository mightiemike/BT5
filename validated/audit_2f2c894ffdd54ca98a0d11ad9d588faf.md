### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase-Proof Security Check Enforced by `verify_transaction_inclusion_v2` - (File: `contract/src/lib.rs`)

### Summary

The contract exposes two public SPV verification endpoints. `verify_transaction_inclusion_v2` was introduced specifically to mitigate the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase merkle proof. However, the original `verify_transaction_inclusion` (v1) remains fully callable by any unprivileged NEAR account. An attacker can call v1 directly, bypassing the coinbase-proof guard entirely, and obtain a `true` result for a forged transaction inclusion proof.

### Finding Description

`verify_transaction_inclusion_v2` enforces two security checks that v1 does not:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` — ensures proof depth parity.
2. The coinbase transaction must hash to the block's `merkle_root` at position `0` — this constrains the Merkle tree structure and prevents an attacker from substituting an internal node hash as a leaf.

After both checks pass, v2 delegates to v1 internally:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
```

v1 itself performs only a bare Merkle root comparison with no coinbase guard:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

The `#[deprecated]` Rust attribute is a compiler-level hint only. It does not restrict NEAR RPC callers. v1 carries the same `#[pause]` access control as v2 and is equally reachable from any external account.

The contract's own doc-comment on v1 acknowledges the risk:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."

This is the exact 64-byte transaction Merkle proof forgery described at https://www.bitmex.com/blog/64-Byte-Transactions: a crafted 64-byte blob that is simultaneously a valid serialized internal Merkle node and a plausible transaction hash can be passed as `tx_id` with a proof that reconstructs the real `merkle_root`, causing v1 to return `true` for a transaction that was never mined.

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` (v1) to gate a privileged action — releasing bridged funds, minting wrapped tokens, confirming a cross-chain payment — can be fed a forged proof. The attacker receives a `true` result without a real on-chain Bitcoin transaction existing. The broken invariant is: *"a positive SPV result must be resistant to 64-byte Merkle forgery."* v2 enforces this; v1 does not; both are callable.

### Likelihood Explanation

The 64-byte Merkle forgery is a publicly documented, well-understood Bitcoin attack vector. Any attacker aware of it can craft the necessary inputs without privileged access. The only prerequisite is that the target block's `merkle_root` is known (it is stored on-chain in `headers_pool`) and that the attacker can construct a 64-byte blob whose double-SHA256 hash, when used as a leaf in a Merkle proof, reconstructs that root. This is a standard offline computation requiring no special permissions.

### Recommendation

Remove `verify_transaction_inclusion` from the public contract ABI entirely, or gate it with an access-control role that prevents unprivileged callers from invoking it. The simplest fix is to change its visibility from `pub` to `pub(crate)` (or remove the `#[near]` export), forcing all external callers to use `verify_transaction_inclusion_v2`.

Alternatively, if backward compatibility must be preserved, apply the same coinbase-proof check inside v1 itself so both paths enforce the same invariant.

### Proof of Concept

1. Identify a block already accepted by the contract (its hash is in `mainchain_header_to_height`). Read its `merkle_root` from `headers_pool`.
2. Offline, construct a 64-byte blob `F` such that `double_sha256(F || sibling) == merkle_root` for some chosen `sibling` value. `F` is the forged `tx_id`; `[sibling]` is the one-element `merkle_proof`; `tx_index = 0`.
3. Call `verify_transaction_inclusion` with `tx_id = F`, `tx_block_blockhash = <target block>`, `tx_index = 0`, `merkle_proof = [sibling]`, `confirmations = 1`.
4. The function returns `true` because `compute_root_from_merkle_proof(F, 0, [sibling]) == merkle_root`.
5. A downstream contract that trusts this result releases funds for a Bitcoin transaction that was never broadcast or mined.

Calling `verify_transaction_inclusion_v2` with the same inputs would fail at step:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
```

because the attacker cannot simultaneously satisfy both the coinbase constraint and the forged-leaf constraint. The v1 bypass makes this guard irrelevant.

---

**Relevant code locations:**

`verify_transaction_inclusion` (v1, no coinbase guard, still publicly callable): [1](#0-0) 

`verify_transaction_inclusion_v2` (v2, coinbase guard enforced, then delegates to v1): [2](#0-1) 

`compute_root_from_merkle_proof` (the bare Merkle check that v1 relies on exclusively): [3](#0-2)

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
