### Title
`node_from_bytes` Does Not Validate Full Buffer Consumption, Silently Accepting Non-Canonical Serializations — (`File: src/serde/de.rs`, `src/serde/de_br.rs`)

---

### Summary

`node_from_bytes` and `node_from_bytes_backrefs` parse exactly one CLVM node from a caller-supplied byte slice but never verify that all bytes in the slice were consumed. A byte slice containing a valid CLVM object followed by arbitrary trailing bytes returns `Ok(node)` identically to the canonical encoding. The consensus-critical entry point `run_serialized_chia_program` (and the Python API functions `deser_legacy`, `deser_backrefs`) inherit this gap, silently accepting non-canonical program/argument blobs.

---

### Finding Description

`node_from_bytes` wraps `node_from_stream` over a `Cursor<&[u8]>` but discards the cursor's final position:

```rust
// src/serde/de.rs  lines 44-46
pub fn node_from_bytes(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream(allocator, &mut buffer)   // cursor position after parse is never checked
}
```

`node_from_stream` stops as soon as one complete CLVM object has been parsed; it does not read to EOF. Any bytes after the first complete object are silently ignored and the function returns `Ok`.

The same pattern appears in `node_from_bytes_backrefs`:

```rust
// src/serde/de_br.rs  lines 114-116
pub fn node_from_bytes_backrefs(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream_backrefs(allocator, &mut buffer, |_node| {})
}
```

The repository already provides `is_canonical_serialization` (which explicitly checks `f.get_ref().len() as u64 == f.position()`) and `serialized_length_from_bytes` (which returns the consumed byte count) for callers that need strict validation. However, neither is called inside `node_from_bytes` or `node_from_bytes_backrefs`, and neither is called by the consensus-critical Python entry point `run_serialized_chia_program`:

```rust
// wheel/src/api.rs  lines 54-60
let r: Response = (|| -> PyResult<Response> {
    let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
    let args    = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
    let dialect = ChiaDialect::new(flags);
    Ok(py.detach(|| run_program(&mut allocator, &dialect, program, args, max_cost)))
})()?;
```

No trailing-byte check is performed before or after either `node_from_bytes` call.

The analog to M-04 is direct:

| M-04 (Router) | clvm_rs |
|---|---|
| Exact-output swap may be partially filled | `node_from_bytes` may consume only part of the input |
| Router checks only `amountIn ≤ max` | `node_from_bytes` checks only that one valid node was parsed |
| Missing check: did we receive the full requested output? | Missing check: did we consume all supplied bytes? |
| Partial fill silently treated as success | Trailing bytes silently treated as absent |

---

### Impact Explanation

**Non-canonical serialization acceptance.** Two distinct byte sequences — `canonical_bytes` and `canonical_bytes ++ trailing_garbage` — both deserialize to the same `NodePtr` and produce identical execution results under `run_serialized_chia_program`. The invariant that `node_from_bytes(b)` succeeds only when `b` is a complete, exact serialization of one CLVM object is broken.

**Consensus divergence risk.** If any validation layer in the Chia full node (Python side) calls `is_canonical_serialization` before submitting to `run_serialized_chia_program`, it will reject the padded blob. If another path skips that check and calls `run_serialized_chia_program` directly, the padded blob is accepted. Two nodes following different code paths can reach different accept/reject decisions for the same spend bundle, which is a consensus split.

**Mempool / deduplication anomaly.** A spend bundle whose puzzle reveal is `canonical_bytes ++ \x00` is byte-for-byte distinct from one using `canonical_bytes`, yet both execute identically. Mempool deduplication keyed on raw bytes would treat them as different entries; deduplication keyed on execution result would treat them as duplicates. This inconsistency is exploitable to inflate mempool state.

---

### Likelihood Explanation

The attacker-controlled entry path is direct and requires no privileges: `run_serialized_chia_program` is a public `#[pyfunction]` that accepts raw `&[u8]` for both `program` and `args`. Appending one or more zero bytes to any valid serialized program produces a crafted blob that triggers the missing check. No key material, no privileged role, and no knowledge of internal state is required.

---

### Recommendation

Add a trailing-byte check inside `node_from_bytes` and `node_from_bytes_backrefs`:

```rust
pub fn node_from_bytes(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    let node = node_from_stream(allocator, &mut buffer)?;
    if buffer.position() != b.len() as u64 {
        return Err(EvalErr::SerializationError);
    }
    Ok(node)
}
```

Apply the same fix to `node_from_bytes_backrefs`. This mirrors the check already present in `is_canonical_serialization` (`src/serde/tools.rs` line 235: `f.get_ref().len() as u64 == f.position()`).

---

### Proof of Concept

```rust
use clvmr::Allocator;
use clvmr::serde::{node_from_bytes, node_to_bytes};

fn main() {
    let mut a = Allocator::new();

    // canonical encoding of the atom 0x01
    let canonical: &[u8] = &[0x01];

    // same atom with trailing garbage
    let padded: &[u8] = &[0x01, 0xde, 0xad, 0xbe, 0xef];

    let node_canonical = node_from_bytes(&mut a, canonical).unwrap();
    // This should fail but currently succeeds:
    let node_padded    = node_from_bytes(&mut a, padded).unwrap();

    // Both produce identical NodePtr content
    assert_eq!(
        node_to_bytes(&a, node_canonical).unwrap(),
        node_to_bytes(&a, node_padded).unwrap(),
    );
    // padded blob accepted == non-canonical serialization silently treated as valid
}
```

**Exact root cause locations:**

- Missing check: [1](#0-0) 
- Missing check: [2](#0-1) 
- Consensus entry point that inherits the gap: [3](#0-2) 
- Existing correct check (not reused): [4](#0-3)

### Citations

**File:** src/serde/de.rs (L44-47)
```rust
pub fn node_from_bytes(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream(allocator, &mut buffer)
}
```

**File:** src/serde/de_br.rs (L114-117)
```rust
pub fn node_from_bytes_backrefs(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream_backrefs(allocator, &mut buffer, |_node| {})
}
```

**File:** wheel/src/api.rs (L54-60)
```rust
    let r: Response = (|| -> PyResult<Response> {
        let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
        let args = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
        let dialect = ChiaDialect::new(flags);

        Ok(py.detach(|| run_program(&mut allocator, &dialect, program, args, max_cost)))
    })()?;
```

**File:** src/serde/tools.rs (L231-235)
```rust
        if (f.get_ref().len() as u64) < f.position() {
            return false;
        }
    }
    f.get_ref().len() as u64 == f.position()
```
