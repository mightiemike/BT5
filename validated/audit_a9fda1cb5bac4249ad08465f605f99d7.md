### Title
Spice Block `prev_state_root` Hardcoded to Default and Validation Unconditionally Skipped — (`File: core/primitives/src/block.rs`)

### Summary
Two unresolved `TODO(spice)` comments in `Block::produce` and `Block::check_validity` together eliminate the `prev_state_root` integrity invariant for all Spice blocks. During production the field is set to `CryptoHash::default()` (32 zero bytes); during validation the check is unconditionally bypassed. Any node that receives a Spice block cannot verify that the committed state root is correct, breaking the canonical hash-domain commitment that every other block version enforces.

### Finding Description

**Production side** — `Block::produce` in `core/primitives/src/block.rs`:

```rust
let prev_state_root = if spice_info.is_some() {
    // TODO(spice): include state root from the relevant previous executed block.
    CryptoHash::default()          // ← 32 zero bytes, not the real state root
} else {
    chunks_wrapper.compute_state_root()
};
``` [1](#0-0) 

**Validation side** — `Block::check_validity` in the same file:

```rust
// TODO(spice): check that block's state_root matches state_root corresponding to chunks of
// the appropriate executed block from the past.
if !self.is_spice_block() {
    let state_root = self.chunks().compute_state_root();
    if self.header().prev_state_root() != &state_root {
        return Err(InvalidStateRoot);
    }
}
``` [2](#0-1) 

The guard `if !self.is_spice_block()` causes the entire `InvalidStateRoot` branch to be dead code for every `BlockV4` that carries a `SpiceCoreStatements` body. `is_spice_block()` is determined by the block body variant, which is attacker-controlled (a block producer controls what body they sign). [3](#0-2) 

### Impact Explanation

`prev_state_root` is included in the `BlockHeader` and committed to by the block hash. For non-Spice blocks this field is the Merkle root of all shard state roots, making it the canonical proof that the header commits to a specific world-state. For Spice blocks:

- The field is always `0x00…00` regardless of actual shard state.
- `check_validity` never compares it against anything.
- Every downstream consumer that reads `header().prev_state_root()` for a Spice block receives a meaningless zero hash.

A block producer can sign a Spice block whose `prev_state_root` diverges arbitrarily from the true post-state of the previous executed block. All validating nodes accept it without error. This breaks the hash-domain commitment invariant that ties block headers to trie state, which is the foundation of light-client proofs, state-sync integrity checks, and cross-shard receipt routing.

### Likelihood Explanation

The Spice protocol path is already wired into `Block::produce` and `BlockBody::new_for_spice`. Once the `SpiceChunkValidation` feature flag is activated on mainnet, every block produced under Spice will carry `CryptoHash::default()` as its `prev_state_root`. The bypass in `check_validity` is unconditional — no configuration or flag can re-enable the check without a code change.

### Recommendation

1. Implement the deferred logic: resolve the actual state root from the most recently certified executed block and pass it into `Block::produce` so `prev_state_root` is set to a meaningful value.
2. In `check_validity`, replace the `if !self.is_spice_block()` guard with a positive check that compares `header().prev_state_root()` against the resolved state root for Spice blocks.
3. Add a schema-level assertion (e.g., via `ProtocolSchema` or a unit test) that `prev_state_root != CryptoHash::default()` for any produced Spice block, to catch regressions before activation.

### Proof of Concept

```
1. Activate SpiceChunkValidation feature flag (or run a local devnet with it enabled).
2. Produce any Spice block via Block::produce with spice_info = Some(...).
3. Observe that block.header().prev_state_root() == CryptoHash::default().
4. Call block.check_validity() — it returns Ok(()) without ever entering the
   InvalidStateRoot branch, regardless of what prev_state_root contains.
5. Mutate prev_state_root to any arbitrary 32-byte value in the signed header
   and repeat step 4 — still Ok(()).
```

The divergent Borsh bytes are the 32-byte `prev_state_root` field inside `BlockHeaderInner` serialized as part of every Spice `BlockV4`, always encoding `[0u8; 32]` instead of the real trie Merkle root. [4](#0-3) [1](#0-0) [2](#0-1)

### Citations

**File:** core/primitives/src/block.rs (L134-137)
```rust
        // TODO(spice): Once spice is released remove Option.
        // Spice block is created IFF this is Some.
        spice_info: Option<SpiceNewBlockProductionInfo>,
    ) -> Self {
```

**File:** core/primitives/src/block.rs (L252-257)
```rust
        let prev_state_root = if spice_info.is_some() {
            // TODO(spice): include state root from the relevant previous executed block.
            CryptoHash::default()
        } else {
            chunks_wrapper.compute_state_root()
        };
```

**File:** core/primitives/src/block.rs (L573-578)
```rust
    pub fn is_spice_block(&self) -> bool {
        match self {
            Block::BlockV1(_) | Block::BlockV2(_) | Block::BlockV3(_) => false,
            Block::BlockV4(block) => block.body.is_spice_block(),
        }
    }
```

**File:** core/primitives/src/block.rs (L601-612)
```rust
    /// Checks that block content matches block hash, with the possible exception of chunk signatures
    pub fn check_validity(&self) -> Result<(), BlockValidityError> {
        // Check that state root stored in the header matches the state root of the chunks
        // With spice chunks wouldn't contain prev_state_roots.
        // TODO(spice): check that block's state_root matches state_root corresponding to chunks of
        // the appropriate executed block from the past.
        if !self.is_spice_block() {
            let state_root = self.chunks().compute_state_root();
            if self.header().prev_state_root() != &state_root {
                return Err(InvalidStateRoot);
            }
        }
```
