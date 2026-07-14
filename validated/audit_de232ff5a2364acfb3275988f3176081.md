### Title
`op_substr` Hardcoded Cost of 1 Enables Undercharged Execution — (File: `src/more_ops.rs`)

### Summary
The `op_substr` operator (opcode 12) returns a fixed cost of `1` regardless of input atom size or result size. This is anomalously low compared to every other operator in the VM and does not account for the real allocator work performed by `new_substr` (atom-limit check, `AtomBuf` slot consumption, `NodePtr` creation). An attacker can craft a CLVM program that calls `substr` millions of times within a normal cost budget, forcing validators to perform far more allocator work than the declared cost implies.

### Finding Description

In `src/more_ops.rs`, `op_substr` unconditionally returns `Reduction(1, r)`:

```rust
pub fn op_substr(...) -> Response {
    ...
    let r = a.new_substr(a0, start as u32, end as u32)?;
    let cost: Cost = 1;
    Ok(Reduction(cost, r))
}
``` [1](#0-0) 

The cost is `1` for any input — a 1-byte atom or a 1 MB atom produces the same declared cost. The test vectors confirm this:

```
substr "abcdefghijkl" 0 12 => "abcdefghijkl" | 1
substr "foobar" 1 => "oobar" | 1
``` [2](#0-1) 

`new_substr` is not free. The allocator test `test_allocate_substr_limit` proves that each `new_substr` call consumes an atom slot and calls `check_atom_limit()`:

```rust
assert_eq!(a.new_substr(atom, 1, 2).unwrap_err(), EvalErr::TooManyAtoms);
``` [3](#0-2) 

The hard atom ceiling is `MAX_NUM_ATOMS = 62_500_000`. [4](#0-3) 

For comparison, `op_strlen` — which also reads an atom length (O(1) work) — costs `STRLEN_BASE_COST (173) + size * STRLEN_COST_PER_BYTE (1)`, and calls `malloc_cost` to account for the result allocation:

```rust
let cost = STRLEN_BASE_COST + size as Cost * STRLEN_COST_PER_BYTE;
Ok(malloc_cost(a, cost, size_node))
``` [5](#0-4) 

`op_substr` charges `1` where `op_strlen` charges `≥173`. Both consume an atom slot for their result.

The run-program loop adds `OP_COST = 1` per operator invocation: [6](#0-5) 

So the total cost per `substr` call through `run_program` is `2`. With a typical Chia cost budget of ~11 billion, an attacker can invoke `substr` up to ~5.5 billion times before the cost budget is exhausted — capped in practice by the 62.5M atom limit, but that still means 62.5M real allocator operations for a total declared cost of only ~125M.

### Impact Explanation

Validators executing attacker-crafted coin spends in consensus mode must process up to 62.5M `new_substr` calls (atom-limit checks, `AtomBuf` slot writes, `NodePtr` construction) for a cost budget expenditure of only ~125M — roughly 88× cheaper per unit of allocator work than `strlen`. This creates a resource-exhaustion disparity: the cost model does not bound the real CPU/memory work performed by the validator, allowing a malicious spender to force disproportionate validator load at minimal declared cost.

### Likelihood Explanation

`op_substr` (opcode 12) is a standard operator available in all execution modes — consensus, mempool, and softfork — with no flag guard. Any attacker who can submit a coin spend (i.e., any Chia network participant) can craft a program that loops `substr` calls. The entry path is direct and requires no special privileges. [7](#0-6) 

### Recommendation

- **Short term:** Apply a cost formula to `op_substr` proportional to the size of the input atom (similar to `strlen`'s `BASE_COST + size * COST_PER_BYTE`), and call `malloc_cost` to account for the result atom allocation.
- **Long term:** Audit all operators that call `new_substr`, `new_concat`, or other allocator primitives to ensure their declared cost covers the allocator work performed, and add fuzz-based cost-regression tests.

### Proof of Concept

A CLVM program of the form:

```
(a (q . (substr LARGE_ATOM 0 N)) ())
```

repeated in a loop (via `a`/apply recursion) up to the atom limit, pays only `2` cost units per iteration while forcing the validator to execute `new_substr` → `check_atom_limit` → `AtomBuf` push on every call. With a 62.5M atom budget and cost=2 per call, the total declared cost is ~125M — well within the 11B block cost limit — while the validator performs 62.5M allocator operations.

### Citations

**File:** src/more_ops.rs (L841-851)
```rust
pub fn op_strlen(
    a: &mut Allocator,
    input: NodePtr,
    _max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let [n] = get_args::<1>(a, input, "strlen")?;
    let size = atom_len(a, n, "strlen")?;
    let size_node = a.new_number(size.into())?;
    let cost = STRLEN_BASE_COST + size as Cost * STRLEN_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, size_node))
```

**File:** src/more_ops.rs (L880-884)
```rust
    } else {
        let r = a.new_substr(a0, start as u32, end as u32)?;
        let cost: Cost = 1;
        Ok(Reduction(cost, r))
    }
```

**File:** op-tests/test-more-ops.txt (L862-865)
```text
substr "abcdefghijkl" 0 => "abcdefghijkl" | 1
substr "abcdefghijkl" -1 => FAIL
substr "abcdefghijkl" 12 => ( ) | 1
substr "abcdefghijkl" 11 => 108 | 1
```

**File:** src/allocator.rs (L17-18)
```rust
const MAX_NUM_ATOMS: usize = 62500000;
const MAX_NUM_PAIRS: usize = 62500000;
```

**File:** src/allocator.rs (L1714-1726)
```rust
    #[test]
    fn test_allocate_substr_limit() {
        let mut a = Allocator::new();

        for _ in 0..MAX_NUM_ATOMS - 3 {
            // exhaust the number of atoms allowed to be allocated
            let _ = a.new_atom(b"foo").unwrap();
        }
        let atom = a.new_atom(b"foo").unwrap();
        assert_eq!(a.new_substr(atom, 1, 2).unwrap_err(), EvalErr::TooManyAtoms);
        assert_eq!(a.u8_vec.len(), 0);
        assert_eq!(a.ghost_atoms, MAX_NUM_ATOMS);
    }
```

**File:** src/run_program.rs (L21-21)
```rust
const OP_COST: Cost = 1;
```

**File:** src/chia_dialect.rs (L202-202)
```rust
            12 => op_substr,
```
