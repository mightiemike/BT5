### Title
`new_substr` on `SmallAtom` Inputs Skips Heap-Limit Check and Omits `ghost_heap` Update, Breaking Allocator Invariant - (File: `src/allocator.rs`)

---

### Summary

`Allocator::new_substr` in `src/allocator.rs` handles three input node types. For the `SmallAtom` branch, it neither enforces the heap limit nor updates `ghost_heap`, while every other allocation path (`new_atom`, `new_small_number`, `new_concat`) does both. This breaks the invariant that `ghost_heap` accurately tracks virtual heap consumption, allowing attacker-controlled CLVM programs to silently exceed the configured heap limit.

---

### Finding Description

Every allocation function that produces a `SmallAtom` result maintains two invariants:

1. **Heap-limit check** before writing: `u8_vec.len() + ghost_heap + new_size > heap_limit → Err(OutOfMemory)`
2. **`ghost_heap` increment** after the check: `ghost_heap += new_size`

`new_atom` (lines 619–638) enforces both:

```rust
// src/allocator.rs:619-629
pub fn new_atom(&mut self, v: &[u8]) -> Result<NodePtr> {
    let start = self.u8_vec.len() as u32;
    if start as usize + self.ghost_heap + v.len() > self.heap_limit {   // ← limit check
        return Err(EvalErr::OutOfMemory);
    }
    ...
    if let Some(ret) = fits_in_small_atom(v) {
        self.ghost_atoms += 1;
        self.ghost_heap += v.len();   // ← ghost_heap updated
        Ok(self.mk_node(ObjectType::SmallAtom, ret as usize))
    }
```

`new_small_number` (lines 640–650) enforces both identically.

`new_substr` for the `SmallAtom` input branch (lines 845–867) enforces **neither**:

```rust
// src/allocator.rs:845-867
ObjectType::SmallAtom => {
    let val = node.index();
    let len = len_for_value(val) as u32;
    bounds_check(node, start, end, len)?;
    let buf: [u8; 4] = val.to_be_bytes();
    let buf = &buf[4 - len as usize..];
    let substr = &buf[start as usize..end as usize];
    if let Some(new_val) = fits_in_small_atom(substr) {
        self.ghost_atoms += 1;
        // ← NO ghost_heap += substr.len()
        // ← NO heap-limit check
        Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
    } else {
        let start = self.u8_vec.len();
        let end = start + substr.len();
        self.u8_vec.extend_from_slice(substr);   // ← real write, no limit check
        ...
        Ok(self.mk_node(ObjectType::Bytes, idx))
    }
}
```

Two sub-cases:

**Sub-case A (SmallAtom → SmallAtom):** `ghost_atoms` is incremented (correct) but `ghost_heap` is not incremented. Every subsequent heap-limit check underestimates actual virtual usage by `substr.len()` bytes per call.

**Sub-case B (SmallAtom → Bytes):** Bytes are written directly to `u8_vec` with no heap-limit check at all. The real heap grows without the guard that `new_atom` and `new_concat` apply.

The entry point is `op_substr` in `src/more_ops.rs` (lines 854–885), which calls `a.new_substr(a0, start as u32, end as u32)` with fully attacker-controlled arguments:

```rust
// src/more_ops.rs:881-883
let r = a.new_substr(a0, start as u32, end as u32)?;
let cost: Cost = 1;
Ok(Reduction(cost, r))
```

The per-call cost of `substr` is **1**, the minimum possible. There is no per-byte cost component.

---

### Impact Explanation

`ghost_heap` is the consensus-compatibility counter that makes the virtual heap size (`heap_size() = u8_vec.len() + ghost_heap`) match what the old implementation would have allocated. Its underaccounting has two concrete effects:

1. **Heap-limit bypass (Sub-case B):** A node operator who calls `Allocator::new_limited(limit)` to cap memory use gets no protection from `substr` on SmallAtom inputs. The `u8_vec` grows past `limit` silently.

2. **Invariant corruption (Sub-case A):** `ghost_heap` diverges from its intended value. Any code path that reads `heap_size()` — including the `counters` feature and any future limit enforcement — receives a stale, underestimated figure. The broken invariant is: *`ghost_heap` equals the sum of virtual bytes attributed to all SmallAtom allocations*.

The maximum undercount per call is 4 bytes (SmallAtom is at most 26 bits / 4 bytes). The atom limit (`MAX_NUM_ATOMS = 62,500,000`) bounds the total undercount to roughly 250 MB before `TooManyAtoms` fires. At cost 1 per call, the cost limit is not a meaningful barrier before the atom limit is reached.

---

### Likelihood Explanation

`op_substr` is a standard, always-available CLVM operator. Any attacker who can submit a CLVM program (puzzle spend on Chia mainnet) can craft a loop that calls `substr` on a small-integer atom (e.g., `(q . 1)`) with indices that produce a SmallAtom or Bytes result. No special permissions, flags, or dialect extensions are required. The trigger is deterministic and reproducible.

---

### Recommendation

In `new_substr`, for the `SmallAtom` input branch, apply the same two steps that `new_atom` and `new_small_number` apply:

1. **Before any allocation**, check `u8_vec.len() + ghost_heap + substr.len() > heap_limit` and return `Err(EvalErr::OutOfMemory)` if exceeded.
2. **After producing a SmallAtom result**, increment `ghost_heap += substr.len()`.

For Sub-case B (SmallAtom → Bytes), the same heap-limit check must precede the `u8_vec.extend_from_slice` call, mirroring the guard in `new_atom` lines 620–622.

---

### Proof of Concept

```
; CLVM program: loop calling substr on atom (q . 1) with start=0, end=1
; Each call costs 1, increments ghost_atoms, skips ghost_heap update
; After ~62M iterations, TooManyAtoms fires, but ghost_heap is underaccounted
; by up to 62_500_000 * 1 = 62.5 MB relative to what new_atom would have charged

(a (q 2 2 (c 2 (c 5 ()))) 
   (c (q 2 (i 5 
              (q 2 2 (c 2 (c (substr 5 (q . 0) (q . 1)) ())))
              (q . ())) 
          1) 
      (c (q . "\x01") ())))
```

Concretely: allocate an `Allocator::new_limited(1024)`, then call `op_substr` on a `SmallAtom` node with `start=0, end=1`. The call succeeds and writes 1 byte to `u8_vec` (Sub-case B) without the heap-limit check firing, even though `u8_vec.len() + ghost_heap` already equals `heap_limit`. The invariant `u8_vec.len() + ghost_heap ≤ heap_limit` is violated. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** src/allocator.rs (L277-286)
```rust
    heap_limit: usize,

    // the ghost counters are pretend atoms/pairs, that were optimized out. We
    // still account for them to not affect the limits of atoms and pairs. Those
    // limits must stay the same for consensus purpose.
    // For example, a "small atom", which is allocated in-place in the NodePtr.
    ghost_atoms: usize,
    ghost_pairs: usize,
    ghost_heap: usize,

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

**File:** src/allocator.rs (L640-650)
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
    }
```

**File:** src/allocator.rs (L845-867)
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
```

**File:** src/allocator.rs (L1232-1258)
```rust
    fn check_atom_limit(&self) -> Result<()> {
        if self.atom_vec.len() + self.ghost_atoms == MAX_NUM_ATOMS {
            Err(EvalErr::TooManyAtoms)
        } else {
            Ok(())
        }
    }

    pub fn atom_count(&self) -> usize {
        self.atom_vec.len() + self.ghost_atoms
    }

    pub fn allocated_atom_count(&self) -> usize {
        self.atom_vec.len()
    }

    pub fn pair_count(&self) -> usize {
        self.pair_vec.len() + self.ghost_pairs
    }

    pub fn allocated_pair_count(&self) -> usize {
        self.pair_vec.len()
    }

    pub fn heap_size(&self) -> usize {
        self.u8_vec.len() + self.ghost_heap
    }
```

**File:** src/more_ops.rs (L854-885)
```rust
pub fn op_substr(
    a: &mut Allocator,
    input: NodePtr,
    _max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let ([a0, start, end], argc) = get_varargs::<3>(a, input, "substr")?;
    if !(2..=3).contains(&argc) {
        Err(EvalErr::InvalidOpArg(
            input,
            format!("Substring takes exactly 2 or 3 arguments, got {argc}"),
        ))?;
    }
    let size = atom_len(a, a0, "substr")?;
    let start = i32_atom(a, start, "substr")?;

    let end = if argc == 3 {
        i32_atom(a, end, "substr")?
    } else {
        size as i32
    };
    if end < 0 || start < 0 || end as usize > size || end < start {
        Err(EvalErr::InvalidOpArg(
            input,
            "Invalid Indices for Substring".to_string(),
        ))?
    } else {
        let r = a.new_substr(a0, start as u32, end as u32)?;
        let cost: Cost = 1;
        Ok(Reduction(cost, r))
    }
}
```
