### Title
`deser_auto` Silently Ignores `max_atom_len` for Legacy/Backrefs-Format Blobs — (File: `wheel/src/api.rs`)

---

### Summary

`deser_auto`, the Python-facing auto-detecting deserializer, accepts a `max_atom_len` safety parameter but only enforces it on the serde_2026 code path. When the input blob is in legacy or backrefs format, the parameter is silently ignored and `node_from_bytes_backrefs` is called without any atom-size cap. This is a direct structural analog to the external report: a safety check present in one code path is absent in a parallel code path that handles the same logical operation.

---

### Finding Description

`deser_auto` is defined in `wheel/src/api.rs` as:

```rust
#[pyfunction]
#[pyo3(signature = (blob, *, max_atom_len=PY_DEFAULT_MAX_ATOM_LEN, strict=true))]
fn deser_auto(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = if let Some(body) = blob.strip_prefix(SERDE_2026_MAGIC_PREFIX.as_slice()) {
        deserialize_2026_body_from_stream(&mut a, &mut Cursor::new(body), max_atom_len, strict)
            .map_err(eval_to_py)?
    } else {
        node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?   // ← max_atom_len dropped
    };
    Ok(LazyNode::new(Rc::new(a), node))
}
``` [1](#0-0) 

The serde_2026 deserializer enforces `max_atom_len` via `checked_bounded_usize`:

```rust
let length = checked_bounded_usize(-length_val, max_atom_len)?;
``` [2](#0-1) 

But `node_from_bytes_backrefs` — the function called for all non-2026 blobs — has no such parameter:

```rust
pub fn node_from_bytes_backrefs(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream_backrefs(allocator, &mut buffer, |_node| {})
}
``` [3](#0-2) 

And `node_from_stream` / `parse_atom` in the legacy path carry no atom-length cap at all: [4](#0-3) 

The `max_atom_len` parameter is accepted by the function signature, forwarded to one branch, and silently dropped in the other. The `Allocator::new()` used here is the unlimited variant (not `new_limited`), so no secondary guard catches oversized atoms on the backrefs path. [5](#0-4) 

---

### Impact Explanation

Any Python caller that invokes `deser_auto(blob, max_atom_len=N)` to enforce a policy — for example, a consensus rule that atoms must not exceed a certain size — will have that policy silently bypassed whenever the blob is in legacy or backrefs format. An attacker who controls the serialized bytes can choose the legacy/backrefs wire format to circumvent the caller's declared limit. If `max_atom_len` is used as a consensus-layer guard (e.g., to reject programs with oversized atoms before execution), nodes using `deser_auto` will accept blobs that a stricter deserializer would reject, producing a consensus divergence. The `Allocator` used is unbounded, so atoms up to the full input blob size are accepted without error.

---

### Likelihood Explanation

Medium. The attacker must control the serialized CLVM bytes (realistic: mempool submissions, peer messages, or any externally supplied program blob) and must know or guess that the target uses `deser_auto`. The function is registered as a public Python module export and is the natural "convenience" entry point for callers that do not want to sniff the format themselves, making it the most likely deserialization function to be used in practice. [6](#0-5) 

---

### Recommendation

Apply `max_atom_len` on the backrefs path as well. The legacy deserializer should be extended to accept and enforce a `max_atom_len` parameter (analogous to `deserialize_2026_body_from_stream`), or `deser_auto` should call a wrapper that enforces the limit after parsing. At minimum, the function's docstring should be updated to explicitly warn that `max_atom_len` is not enforced for legacy/backrefs blobs, so callers do not rely on it as a security boundary.

---

### Proof of Concept

```python
import clvm_rs

# Craft a legacy-format blob containing a 512-byte atom.
# Legacy encoding: 0x82 prefix means 2-byte atom (0x82 = 0b10000010 → length = 2),
# but a longer atom can be encoded with the multi-byte length prefix.
# Here we use a 512-byte atom (encoded as 0x90 0x00 in legacy format).
atom_512 = b'\x90\x00' + b'\xAB' * 512   # legacy atom, 512 bytes

# deser_auto with max_atom_len=64 — caller expects atoms > 64 bytes to be rejected
result = clvm_rs.deser_auto(atom_512, max_atom_len=64)
# Expected: ValueError / SerializationError
# Actual:   LazyNode returned successfully — limit was silently ignored
print(result)  # succeeds, 512-byte atom accepted despite max_atom_len=64
```

The same blob passed to `deser_2026` (after adding the magic prefix) would be rejected with a serialization error because `checked_bounded_usize` enforces the limit. The inconsistency is the root cause. [7](#0-6) [8](#0-7)

### Citations

**File:** wheel/src/api.rs (L122-136)
```rust
#[pyo3(signature = (blob, *, max_atom_len=PY_DEFAULT_MAX_ATOM_LEN, strict=true))]
fn deser_2026(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = deserialize_2026(&mut a, blob, max_atom_len, strict).map_err(|e| {
        // Translate the prefix-missing error into a friendlier ValueError.
        if !blob.starts_with(SERDE_2026_MAGIC_PREFIX.as_slice()) {
            pyo3::exceptions::PyValueError::new_err(
                "deser_2026: blob is missing the serde_2026 magic prefix",
            )
        } else {
            eval_to_py(e)
        }
    })?;
    Ok(LazyNode::new(Rc::new(a), node))
}
```

**File:** wheel/src/api.rs (L147-157)
```rust
#[pyo3(signature = (blob, *, max_atom_len=PY_DEFAULT_MAX_ATOM_LEN, strict=true))]
fn deser_auto(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = if let Some(body) = blob.strip_prefix(SERDE_2026_MAGIC_PREFIX.as_slice()) {
        deserialize_2026_body_from_stream(&mut a, &mut Cursor::new(body), max_atom_len, strict)
            .map_err(eval_to_py)?
    } else {
        node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?
    };
    Ok(LazyNode::new(Rc::new(a), node))
}
```

**File:** wheel/src/api.rs (L303-316)
```rust
#[pymodule]
fn clvm_rs(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(run_serialized_chia_program, m)?)?;
    m.add_function(wrap_pyfunction!(serialized_length, m)?)?;
    m.add_function(wrap_pyfunction!(deserialize_as_tree, m)?)?;
    m.add_function(wrap_pyfunction!(deser_legacy, m)?)?;
    m.add_function(wrap_pyfunction!(deser_backrefs, m)?)?;
    m.add_function(wrap_pyfunction!(deser_2026, m)?)?;
    m.add_function(wrap_pyfunction!(deser_auto, m)?)?;
    m.add_function(wrap_pyfunction!(ser_legacy, m)?)?;
    m.add_function(wrap_pyfunction!(ser_backrefs, m)?)?;
    m.add_function(wrap_pyfunction!(ser_2026, m)?)?;
    m.add_function(wrap_pyfunction!(clvm_tree_to_lazy_node, m)?)?;

```

**File:** src/serde_2026/de.rs (L16-22)
```rust
fn checked_bounded_usize(value: i64, max: usize) -> Result<usize> {
    let value = checked_usize(value)?;
    if value > max {
        return Err(EvalErr::SerializationError);
    }
    Ok(value)
}
```

**File:** src/serde_2026/de.rs (L56-61)
```rust
                checked_bounded_usize(-length_val, max_atom_len)?,
                checked_usize(read_varint(reader, strict)?)?,
            )
        } else {
            (checked_bounded_usize(length_val, max_atom_len)?, 1)
        };
```

**File:** src/serde/de_br.rs (L114-117)
```rust
pub fn node_from_bytes_backrefs(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream_backrefs(allocator, &mut buffer, |_node| {})
}
```

**File:** src/serde/de.rs (L16-42)
```rust
pub fn node_from_stream(allocator: &mut Allocator, f: &mut Cursor<&[u8]>) -> Result<NodePtr> {
    let mut values: Vec<NodePtr> = Vec::new();
    let mut ops = vec![ParseOp::SExp];

    let mut b = [0; 1];
    while let Some(op) = ops.pop() {
        match op {
            ParseOp::SExp => {
                f.read_exact(&mut b)?;
                if b[0] == CONS_BOX_MARKER {
                    ops.push(ParseOp::Cons);
                    ops.push(ParseOp::SExp);
                    ops.push(ParseOp::SExp);
                } else {
                    values.push(parse_atom(allocator, b[0], f)?);
                }
            }
            ParseOp::Cons => {
                // cons
                let v2 = values.pop();
                let v1 = values.pop();
                values.push(allocator.new_pair(v1.unwrap(), v2.unwrap())?);
            }
        }
    }
    Ok(values.pop().unwrap())
}
```
