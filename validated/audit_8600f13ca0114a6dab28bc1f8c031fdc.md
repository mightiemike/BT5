### Title
`verify_transaction_inclusion` Remains Publicly Callable, Bypassing the 64-Byte Merkle Forgery Protection Applied by `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

The contract exposes two public entry points for SPV proof verification. `verify_transaction_inclusion_v2` was introduced to fix the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase merkle proof. However, the original `verify_transaction_inclusion` is still a live, callable public function on the NEAR contract. Any unprivileged caller — including downstream bridge contracts — can invoke it directly and receive a `true` result without the coinbase proof check, bypassing the only protection against Merkle tree inner-node forgery.

---

### Finding Description

The contract's docstring on `verify_transaction_inclusion` explicitly states the vulnerability it leaves open:

> **Warning**: This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.

`verify_transaction_inclusion_v2` was added to close this gap by first verifying a coinbase merkle proof before delegating to the original function:

```rust
// verify_transaction_inclusion_v2 (lines 347–369)
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
```

The `#[deprecated]` Rust attribute on `verify_transaction_inclusion` is a **compiler hint only** — it generates a warning for Rust callers but imposes **no access restriction** on the NEAR contract's public ABI. Any NEAR account can call `verify_transaction_inclusion` directly via a function call action, completely skipping the coinbase proof check.

The two paths perform the same operation (SPV proof verification) but only one applies the critical security check — a direct structural analog to the reported issue where two paths perform the same swap but only one charges the required fee.

---

### Impact Explanation

An attacker who can craft a 64-byte internal Merkle tree node that collides with a desired `tx_id` can call `verify_transaction_inclusion` with that forged `tx_id` and a valid Merkle path to the block's `merkle_root`. The function will return `true`, falsely asserting that a transaction was included in a confirmed Bitcoin block. Any downstream NEAR contract that calls `verify_transaction_inclusion` (e.g., a bridge that releases funds upon proof) will accept the forged proof as valid, enabling theft or unauthorized state transitions.

---

### Likelihood Explanation

The 64-byte transaction forgery attack is a known, documented Bitcoin SPV vulnerability (referenced in the contract's own docstring and the [BitMEX blog post](https://www.bitmex.com/blog/64-Byte-Transactions)). The entry point is fully public and requires no privileged role. Any NEAR account can call it. Downstream contracts integrated before `v2` was introduced are especially likely to still call the deprecated function.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it behind a role check (e.g., `#[private]`) so it cannot be called by external accounts. The `verify_transaction_inclusion_v2` path should be the sole externally reachable verification entry point.

---

### Proof of Concept

1. Attacker identifies a Bitcoin block on the mainchain whose `merkle_root` is known to the contract.
2. Attacker constructs a 64-byte value that is a valid internal Merkle tree node and whose double-SHA256 hash equals a desired forged `tx_id`.
3. Attacker calls `verify_transaction_inclusion` (not `_v2`) with the forged `tx_id`, the block's hash, a valid `tx_index`, and a Merkle path that leads from the forged node to the stored `merkle_root`.
4. The function at lines 318–322 computes `compute_root_from_merkle_proof(forged_tx_id, ...)` and compares it to `header.block_header.merkle_root` — the comparison passes.
5. The function returns `true`, falsely certifying the forged transaction as included.
6. Any downstream contract relying on this result (e.g., a bridge releasing funds) acts on the false proof.

The coinbase check in `verify_transaction_inclusion_v2` (lines 358–365) would have rejected this at step 3, but it is never reached because the attacker called the deprecated function directly. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** btc-types/src/contract_args.rs (L16-48)
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
}
```

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
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
