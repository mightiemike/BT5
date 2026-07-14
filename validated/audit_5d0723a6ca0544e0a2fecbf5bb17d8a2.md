### Title
Missing `ghost_heap` Increment in `new_substr` SmallAtom Path Allows Heap-Limit Bypass — (`File: src/allocator.rs`)

---

### Summary

`Allocator::new_substr` fails to increment `ghost_heap` when the input is a `SmallAtom` and the result also fits in a `SmallAtom`. Every other SmallAtom-producing allocation path (`new_atom`, `new_small_number`) correctly charges `ghost_heap`. Because `ghost_heap` is the sole mechanism that keeps the heap-limit check accurate after the SmallAtom in-pointer optimization, repeated attacker-controlled `substr` calls silently drain the accounting budget, allowing subsequent allocations to bypass the configured heap limit.

---

### Finding Description

`ghost_heap` exists to preserve backward-compatible heap-limit enforcement after the SmallAtom optimization was introduced. The comment in `Allocator` is explicit:

> *"the ghost counters are pretend atoms/pairs, that were optimized out. We still account for them to not affect the limits of atoms and pairs. Those limits must stay the same for consensus purpose."*

Every path that produces a SmallAtom charges `ghost_heap` by the byte-length of the value:

**`new_atom`** — charges correctly: [1](#0-0) 

**`new_small_number`** — charges correctly: [2](#0-1) 

**`new_substr` SmallAtom → SmallAtom** — does **not** charge `ghost_heap`: [3](#0-2) 

The missing line is `self.ghost_heap += (end - start) as usize;`. The `end - start` value is the byte-length of the substring, which is at most 4 bytes for a SmallAtom source.

The heap-limit check that `ghost_heap` feeds into appears in `new_atom`, `new_small_number`, and `new_concat`: [4](#0-3) [5](#0-4) [6](#0-5) 

Each `substr` call on a SmallAtom that produces a SmallAtom result silently under-charges `ghost_heap` by up to 4 bytes, while still consuming one atom slot (via `check_atom_limit`).

---

### Impact Explanation

The heap limit (`heap_limit`) is checked as:

```
u8_vec.len() + ghost_heap + new_bytes > heap_limit  →  OutOfMemory
```

With `ghost_heap` underaccounted, the effective ceiling is raised by the accumulated deficit. An attacker can accumulate a deficit of up to:

```
MAX_NUM_ATOMS × 4 bytes = 62,500,000 × 4 = ~238 MB
```

before the atom-count limit is hit. Any program running under a `heap_limit` set below `u32::MAX` (the default) by that margin can bypass the limit and allocate memory that should have been rejected. This breaks the consensus invariant that heap limits are deterministic and identical across all nodes — a program accepted by a node with the bug would be rejected by a node with the fix, causing **consensus divergence**.

---

### Likelihood Explanation

The `op_substr` operator charges only **1 unit** of CLVM cost per call: [7](#0-6) 

With a typical Chia block cost budget in the billions, an attacker can issue tens of millions of `substr` calls on SmallAtom values (e.g., `(substr (q . 3) 0 1)`) within a single spend, accumulating the full ~238 MB deficit at negligible cost. The atom-count limit (62.5 million) is the binding constraint, not cost. The trigger is fully attacker-controlled via crafted CLVM bytes passed to `run_program`.

---

### Recommendation

In `new_substr`, inside the `ObjectType::SmallAtom` branch where the result fits in a SmallAtom, add the missing `ghost_heap` charge:

```rust
if let Some(new_val) = fits_in_small_atom(substr) {
    self.ghost_atoms += 1;
    self.ghost_heap += (end - start) as usize;  // ← add this line
    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
}
```

This mirrors the accounting in `new_atom` (line 628) and `new_small_number` (line 648). [8](#0-7) 

---

### Proof of Concept

```clvm
; CLVM program: call (substr 3 0 1) in a tight loop via recursion
; Each iteration: one substr on SmallAtom 3 → SmallAtom 1
; ghost_heap deficit: 1 byte per iteration
; atom_count consumed: 1 per iteration
; CLVM cost: ~1 per iteration

(a (q 2 2 (c 2 (c 5 (c 11 ()))))
   (c (q 2 (i (= 11 ())
              (q . ())
              (q 2 2 (c 2 (c (substr 5 (q . 0) (q . 1))
                             (c (- 11 (q . 1)) ())))))
          1)
      1))
```

Invoke with `run_program(allocator_with_small_limit, dialect, program, env, max_cost)` where `heap_limit` is set to, e.g., 200 MB. After ~50 million iterations the `ghost_heap` deficit (~50 MB) allows allocations that should have triggered `OutOfMemory` to succeed, bypassing the limit.

**Root cause**: `src/allocator.rs`, function `new_substr`, lines 852–854 — missing `self.ghost_heap += (end - start) as usize` in the `SmallAtom → SmallAtom` branch. [9](#0-8)

### Citations

**File:** src/allocator.rs (L621-622)
```rust
        if start as usize + self.ghost_heap + v.len() > self.heap_limit {
            return Err(EvalErr::OutOfMemory);
```

**File:** src/allocator.rs (L626-629)
```rust
        if let Some(ret) = fits_in_small_atom(v) {
            self.ghost_atoms += 1;
            self.ghost_heap += v.len();
            Ok(self.mk_node(ObjectType::SmallAtom, ret as usize))
```

**File:** src/allocator.rs (L643-644)
```rust
        if self.u8_vec.len() + self.ghost_heap + len > self.heap_limit {
            return Err(EvalErr::OutOfMemory);
```

**File:** src/allocator.rs (L646-649)
```rust
        self.check_atom_limit()?;
        self.ghost_atoms += 1;
        self.ghost_heap += len;
        Ok(self.mk_node(ObjectType::SmallAtom, v as usize))
```

**File:** src/allocator.rs (L845-868)
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
```

**File:** src/allocator.rs (L882-883)
```rust
        if start + self.ghost_heap + new_size > self.heap_limit {
            return Err(EvalErr::OutOfMemory);
```

**File:** src/more_ops.rs (L881-883)
```rust
        let r = a.new_substr(a0, start as u32, end as u32)?;
        let cost: Cost = 1;
        Ok(Reduction(cost, r))
```
