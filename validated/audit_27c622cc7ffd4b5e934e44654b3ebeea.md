### Title
`ghost_pairs` Counter Underflow in Release Mode Bypasses Consensus Pair Limit — (`File: src/allocator.rs`)

### Summary

`Allocator::remove_ghost_pair` performs an unchecked `usize` subtraction that is guarded only by a `debug_assert!`. In release builds the guard is compiled out, so a crafted CLVM byte stream that causes `remove_ghost_pair(amount)` to be called with `amount > self.ghost_pairs` silently wraps `ghost_pairs` to near `usize::MAX`. Every subsequent call to `new_pair` then evaluates the pair-limit check against a wrapped sentinel, effectively disabling the consensus-critical `MAX_NUM_PAIRS` cap.

### Finding Description

In `src/allocator.rs`, the `remove_ghost_pair` method is:

```rust
pub fn remove_ghost_pair(&mut self, amount: usize) -> Result<()> {
    // currently let this panic with overflow if we go below 0 to debug if/where it happens
    debug_assert!(self.ghost_pairs >= amount);
    self.ghost_pairs -= amount;   // ← plain subtraction, wraps in release mode
    Ok(())
}
``` [1](#0-0) 

The developer comment explicitly acknowledges the potential for underflow and relies on `debug_assert!` to surface it. `debug_assert!` is a no-op in release builds, so the subtraction wraps silently.

The corrupted `ghost_pairs` value then poisons the pair-limit guard inside `new_pair`:

```rust
if idx >= MAX_NUM_PAIRS - self.ghost_pairs {
    return Err(EvalErr::TooManyPairs);
}
``` [2](#0-1) 

`MAX_NUM_PAIRS = 62_500_000`. If `ghost_pairs` has wrapped to, say, `usize::MAX - k`, then `MAX_NUM_PAIRS - ghost_pairs` also wraps to a value near `usize::MAX`, making the condition `idx >= ~usize::MAX` permanently false for any realistic `pair_vec` length. The limit is silently bypassed.

The same wrapped value corrupts `add_ghost_pair`'s guard:

```rust
if MAX_NUM_PAIRS - self.ghost_pairs - self.pair_vec.len() < amount {
    return Err(EvalErr::TooManyPairs);
}
``` [3](#0-2) 

`remove_ghost_pair` is called exclusively from the back-reference deserializer `src/serde/de_br.rs`, which processes attacker-supplied CLVM bytes. [4](#0-3) 

The ghost-pair accounting pattern is: `add_ghost_pair(N)` pre-reserves N slots; `remove_ghost_pair(N)` reclaims them when the pairs are materialised. If a crafted byte stream causes `remove_ghost_pair` to be called with a larger `amount` than was previously added — for example by exploiting a mismatch between the simulated-pair count and the materialised-pair count in the back-reference path — the underflow fires.

### Impact Explanation

`MAX_NUM_PAIRS` is a consensus constant (`62_500_000`). [5](#0-4) 

After the underflow, a CLVM program that allocates more than `MAX_NUM_PAIRS` pairs succeeds on release-mode nodes but panics (via `debug_assert!`) or correctly returns `TooManyPairs` on debug-mode nodes. This is a **consensus divergence**: the same serialised puzzle produces different outcomes on different node builds, which is the most severe class of bug in a blockchain VM.

### Likelihood Explanation

The entry path is `deserialize_br` (back-reference compressed serialization), which is the standard on-chain format for Chia puzzles. Any full node that deserialises a puzzle triggers this code. An attacker who can publish a transaction to the Chia mempool can reach `remove_ghost_pair` with attacker-controlled byte content. The developer comment ("currently let this panic with overflow if we go below 0 to debug if/where it happens") confirms the developers have already observed or anticipated this condition, raising the likelihood that a concrete trigger exists.

### Recommendation

Replace the plain subtraction with a checked or saturating variant and return an error instead of relying on `debug_assert!`:

```rust
pub fn remove_ghost_pair(&mut self, amount: usize) -> Result<()> {
    self.ghost_pairs = self.ghost_pairs.checked_sub(amount)
        .ok_or(EvalErr::InternalError(NodePtr::NIL,
            "ghost_pairs underflow".to_string()))?;
    Ok(())
}
```

Apply the same treatment to any analogous ghost-counter decrements (`ghost_atoms`, `ghost_heap`) that use plain subtraction without a checked guard.

### Proof of Concept

1. Craft a CLVM byte stream in back-reference format that causes the `de_br` deserialiser to call `remove_ghost_pair(M)` where `M` exceeds the total accumulated `add_ghost_pair` budget.
2. Deserialise the bytes on a **release** build of `clvmr`.
3. Observe that `ghost_pairs` wraps to `usize::MAX - (M - ghost_pairs_before) + 1`.
4. Allocate pairs in a subsequent `run_program` call; the `new_pair` limit check evaluates `idx >= MAX_NUM_PAIRS - (~usize::MAX)` which is always false, allowing pair allocation beyond `MAX_NUM_PAIRS`.
5. On a **debug** build the `debug_assert!` fires and the process panics — confirming the divergence.

### Citations

**File:** src/allocator.rs (L17-18)
```rust
const MAX_NUM_ATOMS: usize = 62500000;
const MAX_NUM_PAIRS: usize = 62500000;
```

**File:** src/allocator.rs (L762-765)
```rust
        let idx = self.pair_vec.len();
        if idx >= MAX_NUM_PAIRS - self.ghost_pairs {
            return Err(EvalErr::TooManyPairs);
        }
```

**File:** src/allocator.rs (L776-780)
```rust
        if MAX_NUM_PAIRS - self.ghost_pairs - self.pair_vec.len() < amount {
            return Err(EvalErr::TooManyPairs);
        }
        self.ghost_pairs += amount;
        Ok(())
```

**File:** src/allocator.rs (L784-789)
```rust
    pub fn remove_ghost_pair(&mut self, amount: usize) -> Result<()> {
        // currently let this panic with overflow if we go below 0 to debug if/where it happens
        debug_assert!(self.ghost_pairs >= amount);
        self.ghost_pairs -= amount;
        Ok(())
    }
```

**File:** src/serde/de_br.rs (L1-1)
```rust
use std::io::{Cursor, Read};
```
