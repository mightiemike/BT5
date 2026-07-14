### Title
`is_canonical_serialization` Accepts Backreference Paths That `node_from_bytes_backrefs` Rejects — (`src/serde/tools.rs`)

### Summary

`is_canonical_serialization` validates that a backreference path byte sequence is a **canonically-encoded atom**, but never resolves the path against the tree. `node_from_bytes_backrefs` resolves every backreference path and returns `Err(SerializationBackreferenceError)` when the path traverses into an atom. A crafted byte stream with a syntactically-canonical but semantically-invalid backreference path causes the two functions to disagree on the same input.

---

### Finding Description

**`is_canonical_serialization`** (`src/serde/tools.rs`, lines 219–227):

```rust
} else if b[0] == BACK_REFERENCE {
    // This is a back-ref. We don't actually need to resolve it, just
    // parse the path and move on
    if f.read_exact(&mut b).is_err() {
        return false;
    }
    if !is_canonical_atom(&mut f, b[0]) {
        return false;
    }
``` [1](#0-0) 

When a `0xfe` byte is encountered, the function reads the next byte and calls `is_canonical_atom` — which only checks that the atom's length encoding is non-overlong. It does **not** call `traverse_path` or `traverse_path_with_vec` to verify the path actually resolves to a valid node.

**`node_from_bytes_backrefs`** → `node_from_stream_backrefs` (`src/serde/de_br.rs`, lines 38–43):

```rust
} else if b[0] == BACK_REFERENCE {
    let path = parse_path(f)?;
    let back_reference = traverse_path_with_vec(allocator, path, &mut values)?;
    backref_callback(back_reference);
    allocator.add_ghost_pair(1)?;
    values.push((back_reference, None));
``` [2](#0-1) 

This function resolves every path via `traverse_path_with_vec`. If the path navigates into an atom (e.g., takes a "left" step from an atom node), it returns `Err(SerializationBackreferenceError)`:

```rust
SExp::Atom => {
    return Err(EvalErr::SerializationBackreferenceError);
}
``` [3](#0-2) 

---

### Proof of Concept

**Crafted byte stream:** `[0xff, 0x01, 0xfe, 0x06]`

| Byte | Meaning |
|------|---------|
| `0xff` | `CONS_BOX_MARKER` — expects two children |
| `0x01` | Atom (value 1) — first child |
| `0xfe` | `BACK_REFERENCE` — second child |
| `0x06` | Path byte — `0x06 ≤ 0x7f`, so `is_canonical_atom` returns `true` |

**`is_canonical_serialization([0xff, 0x01, 0xfe, 0x06])`:**
- `0xff` → counter = 2
- `0x01` → `is_canonical_atom(0x01)` → `true`, counter = 1
- `0xfe` → reads `0x06` → `is_canonical_atom(0x06)` → `0x06 ≤ 0x7f` → `true`, counter = 0
- `f.position() == f.get_ref().len()` → 4 == 4
- **Returns `true`**

**`node_from_bytes_backrefs(allocator, [0xff, 0x01, 0xfe, 0x06])`:**
- `0xff` → push Cons, SExp, SExp
- `0x01` → `atom_1` pushed to values stack: `[(atom_1, None)]`
- `0xfe` → `parse_path` reads `0x06` → path = `[0x06]` (binary: `110`)
- `traverse_path_with_vec(allocator, [0x06], &mut [(atom_1, None)])`:
  - Bit 0 of `0x06` is 0 → traverse **left** → `parsing_sexp = true`, `sexp_to_parse = atom_1`
  - Bit 1 of `0x06` is 1 → traverse **right** from `atom_1`
  - `allocator.sexp(atom_1)` = `SExp::Atom` → **`Err(SerializationBackreferenceError)`**

The invariant `is_canonical_serialization(b) == true ⟺ node_from_bytes_backrefs(alloc, b).is_ok()` is broken.

---

### Impact Explanation

`is_canonical_serialization` is a public Rust API exported from `src/serde/mod.rs`:

```rust
pub use tools::{
    is_canonical_serialization, serialized_length_from_bytes, serialized_length_from_bytes_trusted,
    tree_hash_from_stream,
};
``` [4](#0-3) 

Any downstream caller (e.g., a mempool validator or spend-bundle pre-checker) that uses `is_canonical_serialization` as a gate before calling `node_from_bytes_backrefs` will accept a spend bundle that the deserializer then rejects. Conversely, a node that skips `is_canonical_serialization` and calls `node_from_bytes_backrefs` directly will reject the same bundle. This is a concrete API-equivalence divergence path.

The existing `serialized_length_from_bytes` function correctly resolves backreferences via `traverse_path` and would also reject this input — the test in `tools.rs` at line 417–419 even documents this:

```rust
// this is an invalid back-ref
let e =
    serialized_length_from_bytes(&[0xff, 0x01, 0xff, 0xfe, 0x10, 0x80, 0x00]).unwrap_err();
assert_eq!(e.to_string(), "path into atom".to_string());
``` [5](#0-4) 

`is_canonical_serialization` has no equivalent check.

---

### Likelihood Explanation

The discrepancy is trivially reachable with a 4-byte attacker-controlled input. No special privileges, no compromised nodes, and no downstream misuse are required — the divergence is observable by calling both public Rust APIs on the same byte slice. The path byte `0x06` is a valid single-byte canonical atom, so no encoding tricks are needed.

---

### Recommendation

`is_canonical_serialization` must resolve every backreference path against the tree being built, exactly as `serialized_length_from_bytes` does (using `traverse_path`), or it must be documented as a **syntax-only** check that does not guarantee `node_from_bytes_backrefs` will succeed, and all callers must be updated accordingly. The comment "We don't actually need to resolve it" is the root cause of the discrepancy. [6](#0-5)

### Citations

**File:** src/serde/tools.rs (L219-227)
```rust
        } else if b[0] == BACK_REFERENCE {
            // This is a back-ref. We don't actually need to resolve it, just
            // parse the path and move on
            if f.read_exact(&mut b).is_err() {
                return false;
            }
            if !is_canonical_atom(&mut f, b[0]) {
                return false;
            }
```

**File:** src/serde/tools.rs (L416-419)
```rust
        // this is an invalid back-ref
        let e =
            serialized_length_from_bytes(&[0xff, 0x01, 0xff, 0xfe, 0x10, 0x80, 0x00]).unwrap_err();
        assert_eq!(e.to_string(), "path into atom".to_string());
```

**File:** src/serde/de_br.rs (L38-43)
```rust
                } else if b[0] == BACK_REFERENCE {
                    let path = parse_path(f)?;
                    let back_reference = traverse_path_with_vec(allocator, path, &mut values)?;
                    backref_callback(back_reference);
                    allocator.add_ghost_pair(1)?;
                    values.push((back_reference, None));
```

**File:** src/serde/de_br.rs (L157-159)
```rust
                SExp::Atom => {
                    return Err(EvalErr::SerializationBackreferenceError);
                }
```

**File:** src/serde/mod.rs (L39-42)
```rust
pub use tools::{
    is_canonical_serialization, serialized_length_from_bytes, serialized_length_from_bytes_trusted,
    tree_hash_from_stream,
};
```
