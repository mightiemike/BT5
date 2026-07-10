### Title
No Lower-Bound Check on `confirmations` Allows Zero-Confirmation SPV Proof Acceptance - (File: `contract/src/lib.rs`)

### Summary
The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` functions accept a caller-supplied `confirmations: u64` parameter with only an upper-bound check (`<= gc_threshold`) but no lower-bound check. Any unprivileged NEAR caller can supply `confirmations = 0`, causing the confirmation-depth guard to be trivially satisfied for any block on the main chain. A recipient contract consuming the returned `bool` can therefore be made to accept a transaction inclusion proof that carries zero confirmation depth, exposing it to reorganization-based double-spend or replay attacks.

### Finding Description
In `contract/src/lib.rs`, `verify_transaction_inclusion` performs two sequential checks on `confirmations`:

**Check 1 – upper bound only:**
```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

**Check 2 – depth guard:**
```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

When `args.confirmations = 0`, Check 2 reduces to `(any non-negative value) >= 0`, which is always `true`. No minimum is enforced. The same path is reachable through the current production entry point `verify_transaction_inclusion_v2`, which delegates to `verify_transaction_inclusion` after its coinbase-proof guard:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

`verify_transaction_inclusion_v2` is a public, unpausable-by-default view function callable by any NEAR account or cross-contract call with no staking or role requirement. [4](#0-3) 

The `confirmations` field is a plain `u64` in `ProofArgsV2` with no validation annotation: [5](#0-4) 

### Impact Explanation
A recipient contract (bridge, DeFi protocol, escrow) that calls `verify_transaction_inclusion_v2` and trusts the returned `bool` to gate a state-changing action can be exploited as follows:

1. Attacker broadcasts a Bitcoin transaction `T` and waits for it to appear in block `B` at the chain tip (0 confirmations deep).
2. Attacker immediately calls `verify_transaction_inclusion_v2` with `confirmations = 0` and a valid Merkle proof for `T` in `B`.
3. The contract returns `true` because the depth check is trivially satisfied.
4. The recipient contract releases funds or records the event.
5. Block `B` is reorganized away (naturally or via a selfish-mining attack on a low-hashrate chain like Dogecoin or Litecoin supported by this contract). Transaction `T` is invalidated on-chain.
6. The recipient contract's state is now inconsistent with the actual Bitcoin chain state.

The corrupted value is the **proof result** (`true`) returned for a transaction that has no confirmation depth guarantee, violating the core SPV security invariant that inclusion proofs must be backed by sufficient accumulated proof-of-work.

### Likelihood Explanation
Medium. The attack requires:
- A recipient contract that does not independently enforce a minimum confirmation count before acting on the returned boolean (a common integration pattern when the light client is expected to enforce it).
- The attacker to control or predict a transaction's inclusion in a block.

On chains with lower hashrate (Dogecoin, Litecoin, Zcash) supported by this same contract, reorganizations are more frequent, raising the practical likelihood of the post-acceptance reorg step.

### Recommendation
Add a minimum lower-bound check on `confirmations` before the depth guard in `verify_transaction_inclusion`:

```rust
require!(
    args.confirmations >= 1,
    "Confirmations must be at least 1"
);
```

Alternatively, define a `MIN_CONFIRMATIONS` constant (analogous to the recommended `MAX_PROPOSAL_DURATION` in the reference report) and enforce it:

```rust
const MIN_CONFIRMATIONS: u64 = 1;
require!(
    args.confirmations >= MIN_CONFIRMATIONS,
    "Confirmations below minimum"
);
``` [6](#0-5) 

### Proof of Concept

Attacker-controlled call (Borsh-serialized `ProofArgsV2`):
```
tx_id:                  <valid tx hash in block B>
tx_block_blockhash:     <hash of block B at chain tip>
tx_index:               <correct index>
merkle_proof:           <valid Merkle path>
coinbase_tx_id:         <coinbase tx hash of block B>
coinbase_merkle_proof:  <valid coinbase Merkle path>
confirmations:          0          ← no lower-bound check rejects this
```

Execution trace:
1. `verify_transaction_inclusion_v2` passes the coinbase proof check (valid proof supplied).
2. Delegates to `verify_transaction_inclusion`.
3. `args.confirmations (0) <= gc_threshold` → passes.
4. `(tip_height - target_height + 1) >= 0` → always passes.
5. Merkle root recomputed and matched → returns `true`.
6. Recipient contract acts on `true`; block B is later reorganized; funds are lost or state is corrupted.

### Citations

**File:** contract/src/lib.rs (L288-292)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L304-308)
```rust
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );
```

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
```

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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
