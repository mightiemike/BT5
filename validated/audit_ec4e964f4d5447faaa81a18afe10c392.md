### Title
`ghost_heap` Not Updated in `new_substr` SmallAtom Path — Broken Heap-Accounting Invariant - (File: src/allocator.rs)

### Summary

`Allocator::new_substr` in `src/allocator.rs` increments `ghost_atoms` but silently omits the matching `ghost_heap` increment when a `SmallAtom` input produces a `SmallAtom` output. Every other SmallAtom-producing path (`new_atom`, `new_small_number`) increments both counters. The omission breaks the invariant that `ghost_heap` tracks all "virtual" heap bytes, causing `heap_size()` to underreport and the heap-limit guard to be weaker than intended — a direct analog to the StakerVault accounting mismatch where a transfer between categories left `_poolTotalStaked` stale.

---

### Finding Description

The `Allocator` maintains three ghost counters to preserve backward-compatible limits after the SmallAtom optimization was introduced:

```
ghost_atoms  – virtual atom count (SmallAtoms that don't occupy atom_vec)
ghost_pairs  – virtual pair count
ghost_heap   – virtual heap bytes (bytes that would have been in u8_vec)
```

The comment at line 279 is explicit:

> "The ghost counters are pretend atoms/pairs, that were optimized out. We still account for them to not affect the limits of atoms and pairs. Those limits must stay the same for consensus purpose."

Every SmallAtom-producing allocation path increments **both** `ghost_atoms` and `ghost_heap`:

**`new_atom` (SmallAtom branch, lines 626–629):**
```rust
self.ghost_atoms += 1;
self.ghost_heap += v.len();   // ← ghost_heap updated
```

**`new_small_number` (lines 647–648):**
```rust
self.ghost_atoms += 1;
self.ghost_heap += len;       // ← ghost_heap updated
```

**`new_substr` SmallAtom→SmallAtom branch (lines 852–854):**
```rust
self.ghost_atoms += 1;
// ghost_heap is NOT incremented  ← BUG
Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
```

Additionally, the SmallAtom branch of `new_substr` performs no heap-limit check at all, while `new_atom` and `new_small_number` both guard with:
```rust
if self.u8_vec.len() + self.ghost_heap + len > self.heap_limit {
    return Err(EvalErr::OutOfMemory);
}
```

The result is that every `substr` call on a SmallAtom that produces a SmallAtom silently widens the effective heap budget by `end - start` bytes without any enforcement.

---

### Impact Explanation

`heap_size()` is defined as:
```rust
pub fn heap_size(&self) -> usize {
    self.u8_vec.len() + self.ghost_heap
}
```

Because `ghost_heap` is underreported, `heap_size()` returns a value lower than the true "virtual" heap consumption. Two concrete consequences follow:

1. **Heap-limit bypass**: Subsequent calls to `new_atom`, `new_small_number`, or `new_concat` check `u8_vec.len() + ghost_heap + new_bytes > heap_limit`. Each `substr`-on-SmallAtom call that should have incremented `ghost_heap` by N bytes instead leaves N bytes of "free" budget. An attacker can chain many such calls to accumulate a deficit and then allocate real heap bytes that would otherwise be rejected.

2. **`maybe_restore_with_node` underflow risk**: The `AfterNewBytes` branch decrements `ghost_heap` by the atom length after a transparent-checkpoint restore and guards with `if self.ghost_heap < len { return Err(InternalError(...)) }`. If `ghost_heap` has been silently deflated by prior `new_substr` SmallAtom calls, this guard can fire spuriously, turning a valid program into an `InternalError` — a consensus-visible divergence.

---

### Likelihood Explanation

The `substr` CLVM operator is a standard, fully-enabled operator reachable from any attacker-supplied program bytes. A SmallAtom is any positive integer fitting in 26 bits (values 1–67,108,863), and `substr` on such an atom with `start < end ≤ len_for_value(val)` always hits the SmallAtom branch. No special flags, dialect settings, or privileged access are required. The trigger is a single well-formed CLVM expression such as `(substr (q . 0x010203) 0 1)` where the source atom is a SmallAtom and the slice also fits in a SmallAtom.

---

### Recommendation

In the `SmallAtom → SmallAtom` branch of `new_substr`, add the missing heap-limit check and `ghost_heap` increment, mirroring `new_atom` and `new_small_number`:

```rust
ObjectType::SmallAtom => {
    let val = node.index();
    let len = len_for_value(val) as u32;
    bounds_check(node, start, end, len)?;
    let buf: [u8; 4] = val.to_be_bytes();
    let buf = &buf[4 - len as usize..];
    let substr = &buf[start as usize..end as usize];
    if let Some(new_val) = fits_in_small_atom(substr) {
        // Add heap-limit guard and ghost_heap update:
        let substr_len = substr.len();
        if self.u8_vec.len() + self.ghost_heap + substr_len > self.heap_limit {
            return Err(EvalErr::OutOfMemory);
        }
        self.ghost_atoms += 1;
        self.ghost_heap += substr_len;   // ← fix
        Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
    } else { ... }
}
```

---

### Proof of Concept

**Trigger path (attacker-controlled CLVM bytes):**

```
(substr (q . 0x010203) (q . 0) (q . 1))
```

- `(q . 0x010203)` is a 3-byte positive integer ≤ 67,108,863 → stored as `SmallAtom` with index `0x010203`.
- `substr` with `start=0, end=1` extracts byte `0x01`, which also fits in a SmallAtom.
- `new_substr` is called; the `SmallAtom` branch fires; `ghost_atoms += 1` but `ghost_heap` is **not** incremented.

**Accounting divergence:**

| Call | `ghost_atoms` delta | `ghost_heap` delta (actual) | `ghost_heap` delta (expected) |
|---|---|---|---|
| `new_atom(&[0x01, 0x02, 0x03])` | +1 | +3 | +3 |
| `new_substr(atom, 0, 1)` | +1 | **0** | **+1** |

After N such `substr` calls, `heap_size()` is underreported by N bytes, and the heap-limit guard in subsequent `new_atom`/`new_small_number` calls is correspondingly weaker.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/allocator.rs (L279-285)
```rust
    // the ghost counters are pretend atoms/pairs, that were optimized out. We
    // still account for them to not affect the limits of atoms and pairs. Those
    // limits must stay the same for consensus purpose.
    // For example, a "small atom", which is allocated in-place in the NodePtr.
    ghost_atoms: usize,
    ghost_pairs: usize,
    ghost_heap: usize,
```

**File:** src/allocator.rs (L619-629)
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
```

**File:** src/allocator.rs (L640-649)
```rust
    pub fn new_small_number(&mut self, v: u32) -> Result<NodePtr> {
        debug_assert!(v <= NODE_PTR_IDX_MASK);
        let len = len_for_value(v);
        if self.u8_vec.len() + self.ghost_heap + len > self.heap_limit {
            return Err(EvalErr::OutOfMemory);
        }
        self.check_atom_limit()?;
        self.ghost_atoms += 1;
        self.ghost_heap += len;
        Ok(self.mk_node(ObjectType::SmallAtom, v as usize))
```

**File:** src/allocator.rs (L845-854)
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
```

**File:** src/allocator.rs (L1256-1258)
```rust
    pub fn heap_size(&self) -> usize {
        self.u8_vec.len() + self.ghost_heap
    }
```
