### Title
`verify_transaction_inclusion_v2` Incomplete Second-Preimage Fix Allows Internal Node Forgery as Valid Transaction — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` attempts to prevent the Bitcoin Merkle second-preimage attack by requiring the coinbase proof and the target-tx proof to have equal length. However, because `coinbase_tx_id` is entirely caller-supplied and never validated to be the actual coinbase transaction, an attacker can supply an internal Merkle node as `coinbase_tx_id` with a shorter-than-real proof that still verifies against the block root. The equal-length constraint is then satisfied at the shorter depth, and the attacker can simultaneously prove any other internal node as a "transaction." The function returns `true` for a `tx_id` that is not a real Bitcoin transaction.

---

### Finding Description

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` performs no validation of proof length against tree depth:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;
    for proof_hash in merkle_proof {
        ...
        current_position /= 2;
    }
    current_hash
}
``` [1](#0-0) 

Any hash — leaf or internal node — can be used as `transaction_hash`, and any proof length is accepted. This is the root cause.

`verify_transaction_inclusion_v2` attempts to mitigate this with two checks:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` — both proofs must be the same depth.
2. The coinbase proof must verify against the stored block `merkle_root`.

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    "Coinbase merkle proof and transaction merkle proof should have the same length"
);
...
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [2](#0-1) 

The flaw: `coinbase_tx_id` is a field of `ProofArgsV2` that is entirely caller-controlled and never validated to be the actual coinbase transaction:

```rust
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,          // ← fully attacker-controlled
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
``` [3](#0-2) 

An attacker can supply an internal node hash as `coinbase_tx_id` with a proof of depth D-1 (shorter than the real tree depth D). Because `compute_root_from_merkle_proof` accepts any input at any depth, this shorter proof still verifies against the root. The equal-length constraint is then satisfied at depth D-1, and the attacker can simultaneously prove any other internal node as `tx_id` with the same shorter proof.

---

### Impact Explanation

Any NEAR contract or off-chain consumer that calls `verify_transaction_inclusion_v2` and trusts its `true` return value to confirm a Bitcoin transaction is included in a block can be deceived. The attacker can make the contract assert that an internal Merkle node hash — which is not a real Bitcoin transaction — is a confirmed transaction in a given block. Downstream contracts that gate actions (e.g., token minting, bridge unlocks) on SPV proof results are directly affected.

---

### Likelihood Explanation

All inputs required for the attack are publicly available from the Bitcoin RPC: the block's Merkle tree structure and all internal node hashes are computable from the raw block. No privileged access is required. Any unprivileged NEAR account can call `verify_transaction_inclusion_v2` directly. The only prerequisite is that the target block's header has been submitted to the light client contract, which is the normal operating condition.

---

### Recommendation

The coinbase-proof-length approach is sound in principle, but only if `coinbase_tx_id` is guaranteed to be the actual coinbase transaction. The fix must enforce this. Options:

1. **Require the caller to supply the raw coinbase transaction bytes** and verify that `double_sha256(raw_coinbase) == coinbase_tx_id`. This binds the coinbase hash to a real 60+ byte transaction, making it impossible to substitute a 32-byte internal node.
2. **Enforce minimum proof length** equal to `ceil(log2(tx_count))` by requiring the caller to also supply the transaction count and validating it against the coinbase (which encodes the block height in its scriptSig for BIP34 blocks).
3. **Document clearly** that `verify_transaction_inclusion_v2` remains vulnerable and must not be used as a standalone security boundary without external validation of `tx_id` against raw transaction data.

---

### Proof of Concept

Consider a block with 4 transactions `[T0, T1, T2, T3]` (tree depth = 2):

```
Level 0 (leaves): T0,  T1,  T2,  T3
Level 1:          H01=H(T0||T1),  H23=H(T2||T3)
Root:             R = H(H01||H23)
```

Real coinbase proof (depth 2): `coinbase_tx_id=T0`, `coinbase_merkle_proof=[T1, H23]`

**Attacker's call to `verify_transaction_inclusion_v2`:**

```
coinbase_tx_id        = H01   ← internal node, not a real tx
coinbase_merkle_proof = [H23] ← depth 1 proof
tx_id                 = H01   ← same internal node (or H23)
tx_index              = 0
merkle_proof          = [H23] ← depth 1 proof, same length
```

Step-by-step through the contract:

1. **Length check**: `merkle_proof.len() == coinbase_merkle_proof.len()` → `1 == 1` ✓
2. **Coinbase proof check**: `compute_root_from_merkle_proof(H01, 0, [H23])` = `H(H01 || H23)` = `R` == `header.merkle_root` ✓
3. **Tx proof** (inside deprecated `verify_transaction_inclusion`): `compute_root_from_merkle_proof(H01, 0, [H23])` = `R` == `header.merkle_root` ✓
4. **Returns `true`** for `tx_id = H01`, which is an internal Merkle node, not a Bitcoin transaction.

The attacker has successfully forged inclusion of a non-existent transaction. All inputs (`H01`, `H23`) are computable from the public Bitcoin block data. [4](#0-3) [1](#0-0) [5](#0-4)

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

**File:** btc-types/src/contract_args.rs (L26-47)
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
