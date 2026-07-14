Looking at the code I've reviewed, let me now analyze the `op_substr` cost model and the `op_ash`/`op_lsh` cost model carefully, then check `op_modpow` for the most likely undercharged execution analog.

Based on my thorough review of the codebase, I can identify a concrete analog:

---

### Title
`op_substr` Charges Fixed Cost of 1 Regardless of Output Size, Enabling Atom-Limit Exhaustion Below Cost Budget — (`File: src/more_ops.rs`)

### Summary

`op_substr` in `src/more_ops.rs` charges a fixed CLVM cost of `1` for every invocation, regardless of the size of the substring produced or the number of allocator atoms consumed. Because each call increments the allocator's atom count by 1, an attacker can exhaust the `MAX_NUM_ATOMS` limit (62,500,000) with a total declared cost of only ~62,500,000 — far below the typical `max_cost` budget of ~11,000,000,000 used in Chia consensus. This is a direct analog to the "undercharged execution" class: the cost charged does not reflect the actual allocator resource consumed per call.

### Finding Description

In `src/more_ops.rs`, `op_substr` is implemented as:

```rust
pub fn op_substr(
    a: &mut Allocator,
    input: NodePtr,
    _max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    ...
    let r = a.new_substr(a0, start as u32, end as u32)?;
    let cost: Cost = 1;
    Ok(Reduction(cost, r))
}
``` [1](#0-0) 

Every other operator that allocates a new atom charges `MALLOC_COST_PER_BYTE` (10 cost units per byte) for the output. For example, `op_concat` charges `CONCAT_COST_PER_BYTE + MALLOC_COST_PER_BYTE` per byte, and `op_sha256` charges `SHA256_COST_PER_BYTE` per byte plus a `malloc_cost` call. [2](#0-1) [3](#0-2) 

`new_substr` always calls `check_atom_limit()`, which increments the atom count toward `MAX_NUM_ATOMS = 62_500_000`: [4](#0-3) [5](#0-4) 

For `ObjectType::Bytes` atoms (the common case for large atoms), `new_substr` creates a new `AtomBuf` entry pointing into the existing heap — no bytes are copied, but the atom slot is consumed. For `ObjectType::SmallAtom` inputs, it may also write bytes to the heap. [6](#0-5) 

### Impact Explanation

The atom limit (`MAX_NUM_ATOMS = 62,500,000`) is a consensus-critical resource cap. An attacker can write a CLVM program that:

1. Allocates one large atom (e.g., 1 MB via `concat` or `sha256`).
2. Calls `op_substr` in a tight loop, each time producing a 1-byte slice of that atom.
3. Each call costs only `1` CLVM unit but consumes one atom slot.

After 62,500,000 iterations, the atom limit is exhausted at a total cost of ~62,500,000 — roughly **176× cheaper** than the 11,000,000,000 cost budget. The program fails with `TooManyAtoms`, but the node has already spent CPU time processing all 62.5 million `new_substr` calls and their associated allocator bookkeeping. This is the same class as the op-geth report: real node resource consumption (allocator slot writes, limit checks) is not reflected in the declared cost, allowing a cheap program to force disproportionate work.

### Likelihood Explanation

The entry path is direct: any caller of `run_program` (Python bindings via `wheel/`, the Chia full node, or the mempool) passes attacker-controlled CLVM bytes. The `op_substr` opcode (opcode 12) is available in the default operator set with no flag guard. [7](#0-6) 

The `MEMPOOL_MODE` flags do not restrict `op_substr`. [8](#0-7) 

### Recommendation

Charge `op_substr` proportionally to the size of the output atom, consistent with how all other allocation-producing operators are costed. At minimum, add a base cost proportional to `(end - start)` bytes using `MALLOC_COST_PER_BYTE`, and add a non-trivial base cost (e.g., matching `STRLEN_BASE_COST = 173`). This aligns the cost with the allocator slot and bookkeeping work actually performed.

### Proof of Concept

```clvm
; Pseudocode: allocate a 1-byte atom, then substr it 62,500,000 times
; Each substr costs 1; total cost ≈ 62,500,000 << max_cost (11,000,000,000)
(let ((big (concat (q . "\xff") ...)))
  (loop 62500000
    (substr big 0 1)))
```

Concretely, a CLVM program using `(a ...)` recursion that calls opcode 12 (`substr`) with fixed offsets `0` and `1` on a pre-allocated atom will exhaust `MAX_NUM_ATOMS` at a cost budget of ~62.5 million, well within the 11-billion limit, forcing the full node to perform 62.5 million allocator operations before the program terminates. [9](#0-8) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L854-884)
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
```

**File:** src/more_ops.rs (L887-916)
```rust
pub fn op_concat(
    a: &mut Allocator,
    mut input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let mut cost = CONCAT_BASE_COST;
    let mut total_size: usize = 0;
    let mut terms = Vec::<NodePtr>::new();
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        let len = match a.sexp(arg) {
            SExp::Pair(_, _) => {
                return Err(EvalErr::InvalidOpArg(arg, "concat on list".to_string()))?;
            }
            SExp::Atom => a.atom_len(arg),
        };
        cost += CONCAT_COST_PER_ARG;
        cost += len as Cost * (CONCAT_COST_PER_BYTE + MALLOC_COST_PER_BYTE);
        check_cost(cost, max_cost)?;
        if len > 0 {
            // skip NIL arguments, as an optimization
            total_size += len;
            terms.push(arg);
        }
    }

    let new_atom = a.new_concat(total_size, &terms)?;
    Ok(Reduction(cost, new_atom))
}
```

**File:** src/op_utils.rs (L12-13)
```rust
// We ascribe some additional cost per byte for operations that allocate new atoms
pub const MALLOC_COST_PER_BYTE: Cost = 10;
```

**File:** src/allocator.rs (L17-17)
```rust
const MAX_NUM_ATOMS: usize = 62500000;
```

**File:** src/allocator.rs (L799-803)
```rust
    pub fn new_substr(&mut self, node: NodePtr, start: u32, end: u32) -> Result<NodePtr> {
        #[cfg(feature = "allocator-debug")]
        self.validate_node(node);

        self.check_atom_limit()?;
```

**File:** src/allocator.rs (L827-870)
```rust
        match node.object_type() {
            ObjectType::Pair => Err(EvalErr::InternalError(
                node,
                "substr expected atom, got pair".to_string(),
            ))?,
            ObjectType::Bytes => {
                let atom = self.atom_vec[node.index() as usize];
                let atom_len = atom.end - atom.start;
                bounds_check(node, start, end, atom_len)?;
                let idx = self.atom_vec.len();
                self.atom_vec.push(AtomBuf {
                    start: atom.start + start,
                    end: atom.start + end,
                });
                #[cfg(feature = "counters")]
                self.update_max_counts();
                Ok(self.mk_node(ObjectType::Bytes, idx))
            }
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
    }
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L202-202)
```rust
            12 => op_substr,
```
