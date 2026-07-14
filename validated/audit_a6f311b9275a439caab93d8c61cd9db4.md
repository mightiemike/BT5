### Title
Incomplete Ghost-Counter State Update in `new_substr` SmallAtom Path Bypasses Heap Limit Enforcement — (File: `src/allocator.rs`)

---

### Summary

The `Allocator::new_substr` function in `src/allocator.rs` contains two related state-update inconsistencies in its `SmallAtom` handling branch, directly analogous to the "complicated state updates" bug class. Every other SmallAtom-creation path (`new_atom`, `new_small_number`) atomically updates **both** `ghost_atoms` and `ghost_heap`. The `new_substr` SmallAtom→SmallAtom branch increments only `ghost_atoms`, silently omitting the `ghost_heap` update. Additionally, the SmallAtom→Bytes branch extends `u8_vec` without performing the heap-limit check that `new_atom` performs before every heap extension. Both omissions are reachable via attacker-controlled CLVM bytes through the `substr` operator.

---

### Finding Description

**Invariant being broken:** Every allocation of a SmallAtom must update both `ghost_atoms` and `ghost_heap` together. Every extension of `u8_vec` must be preceded by a heap-limit check. These two counters are the "stake balance" equivalents of this codebase — they must be kept in lock-step.

**Correct pattern — `new_atom` (lines 626–629):**

```rust
if let Some(ret) = fits_in_small_atom(v) {
    self.ghost_atoms += 1;
    self.ghost_heap += v.len();   // ← both updated
    Ok(self.mk_node(ObjectType::SmallAtom, ret as usize))
``` [1](#0-0) 

**Correct pattern — `new_small_number` (lines 647–649):**

```rust
self.ghost_atoms += 1;
self.ghost_heap += len;           // ← both updated
Ok(self.mk_node(ObjectType::SmallAtom, v as usize))
``` [2](#0-1) 

**Broken pattern — `new_substr` SmallAtom→SmallAtom branch (lines 852–854):**

```rust
if let Some(new_val) = fits_in_small_atom(substr) {
    self.ghost_atoms += 1;
    // ghost_heap is NOT updated ← missing update
    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
``` [3](#0-2) 

**Broken pattern — `new_substr` SmallAtom→Bytes branch (lines 855–867):**

```rust
} else {
    let start = self.u8_vec.len();
    let end = start + substr.len();
    self.u8_vec.extend_from_slice(substr);  // ← no heap-limit check before extension
    let idx = self.atom_vec.len();
    self.atom_vec.push(AtomBuf { start: start as u32, end: end as u32 });
    Ok(self.mk_node(ObjectType::Bytes, idx))
}
``` [4](#0-3) 

Compare with `new_atom`'s mandatory pre-check before any `u8_vec` extension:

```rust
if start as usize + self.ghost_heap + v.len() > self.heap_limit {
    return Err(EvalErr::OutOfMemory);
}
``` [5](#0-4) 

The `ghost_heap` counter feeds every heap-limit guard in the allocator. When it is underreported, those guards silently pass for programs that should be rejected. [6](#0-5) 

---

### Impact Explanation

**Ghost-heap underreporting (SmallAtom→SmallAtom):** `ghost_heap` accumulates the logical heap cost of SmallAtoms that were optimized out of `u8_vec`. When `new_substr` produces a SmallAtom, it omits this accounting. Every subsequent call to `new_atom`, `new_small_number`, or `new_concat` uses the stale `ghost_heap` value in its limit check, allowing the total logical heap to silently exceed `heap_limit`.

**Unchecked heap extension (SmallAtom→Bytes):** When a SmallAtom substring has its high bit set (e.g., byte `\x80`), `fits_in_small_atom` returns `None` and the code falls into the Bytes branch, which calls `u8_vec.extend_from_slice` without any limit check. This directly extends the real heap past `heap_limit`.

**Quantified bound:** A SmallAtom is at most 4 bytes (26-bit value). The atom count limit is `MAX_NUM_ATOMS = 62_500_000`. An attacker can therefore bypass the heap limit by up to `62_500_000 × 4 = 250 MB`. For the default `heap_limit` of `u32::MAX` (≈4 GiB) this is marginal, but for any caller using `Allocator::new_limited` with a tighter bound (e.g., a consensus-enforced cap), the bypass is proportionally more severe. [7](#0-6) [8](#0-7) 

The `ghost_heap` value is also read during `restore_transparent_checkpoint` and `maybe_restore_with_node`, meaning the underreporting propagates across softfork-guard boundaries and checkpoint restores, compounding across repeated guard entries. [9](#0-8) [10](#0-9) 

---

### Likelihood Explanation

The `substr` operator is a standard CLVM core operator reachable from any attacker-supplied program bytes. The SmallAtom path is triggered whenever the input atom's value fits in 26 bits (i.e., is a small positive integer ≤ `0x3FFFFFF`), which is the common case for integer operands in CLVM programs. The SmallAtom→Bytes sub-path is triggered by taking a suffix of a multi-byte SmallAtom that starts with a byte ≥ `0x80` (e.g., `substr` of `\x01\x80` at offset 1). Both paths are reachable without any special privileges. The cost model limits the number of `substr` calls per execution, but the per-call heap bypass (up to 4 bytes) accumulates across the full atom budget.

---

### Recommendation

Apply the same two-field update pattern used by `new_atom` and `new_small_number` to the SmallAtom→SmallAtom branch of `new_substr`:

```rust
if let Some(new_val) = fits_in_small_atom(substr) {
    self.ghost_atoms += 1;
    self.ghost_heap += (end - start) as usize;  // add missing update
    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
```

Add a heap-limit pre-check to the SmallAtom→Bytes branch of `new_substr`, mirroring `new_atom`:

```rust
} else {
    let start_offset = self.u8_vec.len();
    if start_offset + self.ghost_heap + substr.len() > self.heap_limit {
        return Err(EvalErr::OutOfMemory);
    }
    ...
```

Consider encapsulating all SmallAtom ghost-counter updates into a single internal helper (analogous to the report's recommendation of `_updateDelegatorStake`-style functions) so that every allocation site is forced to go through the same consistent accounting path.

---

### Proof of Concept

```
; CLVM program that calls substr on a SmallAtom to produce a Bytes node
; bypassing the heap-limit check.
; Input atom: 0x0180 (SmallAtom, value 384, bytes \x01\x80)
; substr(1, 2) → \x80 → high bit set → Bytes path, no limit check

(substr (q . 0x0180) (q . 1) (q . 2))
```

Repeated in a loop up to the atom-count limit, each iteration extends `u8_vec` by 1 byte without a heap-limit check, allowing the real heap to grow beyond `heap_limit` when `new_limited` is used with a tight cap. The SmallAtom→SmallAtom variant (e.g., `substr` of `\x01\x02` at offset 0, length 1 → `\x01`, still a SmallAtom) silently omits the `ghost_heap` increment, causing all subsequent limit checks to undercount logical heap usage by 1 byte per call. [11](#0-10)

### Citations

**File:** src/allocator.rs (L17-18)
```rust
const MAX_NUM_ATOMS: usize = 62500000;
const MAX_NUM_PAIRS: usize = 62500000;
```

**File:** src/allocator.rs (L363-365)
```rust
    pub fn new_limited(heap_limit: usize) -> Self {
        // we have a maximum of 4 GiB heap, because pointers are 32 bit unsigned
        assert!(heap_limit <= u32::MAX as usize);
```

**File:** src/allocator.rs (L485-498)
```rust
    pub fn restore_transparent_checkpoint(&mut self, cp: &TransparentCheckpoint) {
        // if any of these asserts fire, it means we're trying to restore to
        // a state that has already been "long-jumped" passed (via another
        // restore to an earlier state). You can only restore backwards in time,
        // not forwards.
        assert!(self.u8_vec.len() >= cp.u8s as usize);
        assert!(self.pair_vec.len() >= cp.pairs as usize);
        assert!(self.atom_vec.len() >= cp.atoms as usize);
        self.ghost_heap += self.u8_vec.len() - cp.u8s as usize;
        self.ghost_pairs += self.pair_vec.len() - cp.pairs as usize;
        self.ghost_atoms += self.atom_vec.len() - cp.atoms as usize;
        self.u8_vec.truncate(cp.u8s as usize);
        self.pair_vec.truncate(cp.pairs as usize);
        self.atom_vec.truncate(cp.atoms as usize);
```

**File:** src/allocator.rs (L599-614)
```rust
                self.restore_transparent_checkpoint(checkpoint);
                if self.ghost_atoms == 0 {
                    return Err(EvalErr::InternalError(
                        NodePtr::NIL,
                        "ghost atom accounting error".to_string(),
                    ));
                }
                self.ghost_atoms -= 1;
                if self.ghost_heap < len {
                    return Err(EvalErr::InternalError(
                        NodePtr::NIL,
                        "ghost heap accounting error".to_string(),
                    ));
                }
                self.ghost_heap -= len;
                Ok(MaybeRestore::Replace(self.new_atom(&saved_bytes[..len])?))
```

**File:** src/allocator.rs (L619-638)
```rust
    pub fn new_atom(&mut self, v: &[u8]) -> Result<NodePtr> {
        let start = self.u8_vec.len() as u32;
        if start as usize + self.ghost_heap + v.len() > self.heap_limit {
            return Err(EvalErr::OutOfMemory);
        }
        let idx = self.atom_vec.len();
        self.check_atom_limit()?;
        if let Some(ret) = fits_in_small_atom(v) {
            self.ghost_atoms += 1;
            self.ghost_heap += v.len();
            Ok(self.mk_node(ObjectType::SmallAtom, ret as usize))
        } else {
            self.u8_vec.extend_from_slice(v);
            let end = self.u8_vec.len() as u32;
            self.atom_vec.push(AtomBuf { start, end });
            #[cfg(feature = "counters")]
            self.update_max_counts();
            Ok(self.mk_node(ObjectType::Bytes, idx))
        }
    }
```

**File:** src/allocator.rs (L647-649)
```rust
        self.ghost_atoms += 1;
        self.ghost_heap += len;
        Ok(self.mk_node(ObjectType::SmallAtom, v as usize))
```

**File:** src/allocator.rs (L845-869)
```rust
            ObjectType::SmallAtom => {
                let val = node.index();
                let len = len_for_value(val) as u32;
                bounds_check(node, start, end, len)?;
                let buf: [u8; 4] = val.to_be_bytes();
                let buf = &buf[4 - len as usize..];
                let substr = &buf[start as usize..end as usize];
                if let Some(new_val) = fits_in_small_atom(substr) {
                    self.ghost_atoms += 1;
                    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
                } else {
                    let start = self.u8_vec.len();
                    let end = start + substr.len();
                    self.u8_vec.extend_from_slice(substr);
                    let idx = self.atom_vec.len();
                    self.atom_vec.push(AtomBuf {
                        start: start as u32,
                        end: end as u32,
                    });
                    #[cfg(feature = "counters")]
                    self.update_max_counts();
                    Ok(self.mk_node(ObjectType::Bytes, idx))
                }
            }
        }
```
