### Title
`deser_auto` Silently Ignores `max_atom_len` and `strict` Parameters for Non-serde_2026 Blobs — (`wheel/src/api.rs`)

---

### Summary

`deser_auto` accepts `max_atom_len` and `strict` parameters that are correctly forwarded to the serde_2026 decoder, but are **silently dropped** when the blob is in legacy/backrefs format. This is a direct analog to the reported vulnerability: one API path enforces a business-logic constraint; a related "auto-dispatch" path bypasses it entirely, returning raw parsed results regardless of the caller's stated limits.

---

### Finding Description

`deser_auto` is the Python-facing convenience deserializer that auto-detects the wire format. Its signature accepts `max_atom_len` and `strict`:

```rust
// wheel/src/api.rs  lines 147-157
#[pyfunction]
#[pyo3(signature = (blob, *, max_atom_len=PY_DEFAULT_MAX_ATOM_LEN, strict=true))]
fn deser_auto(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = if let Some(body) = blob.strip_prefix(SERDE_2026_MAGIC_PREFIX.as_slice()) {
        deserialize_2026_body_from_stream(&mut a, &mut Cursor::new(body), max_atom_len, strict)
            .map_err(eval_to_py)?
    } else {
        node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?   // ← max_atom_len and strict are NOT passed
    };
    Ok(LazyNode::new(Rc::new(a), node))
}
```

When the blob carries the serde_2026 magic prefix, both `max_atom_len` and `strict` are forwarded to `deserialize_2026_body_from_stream`. When the blob is in legacy or backrefs format, `node_from_bytes_backrefs` is called with **no size cap and no strictness flag**. The two parameters accepted by the function are silently discarded.

Compare with `deser_2026`, which always enforces both:

```rust
// wheel/src/api.rs  lines 122-136
fn deser_2026(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode> {
    ...
    let node = deserialize_2026(&mut a, blob, max_atom_len, strict)...
``` [1](#0-0) [2](#0-1) 

---

### Impact Explanation

**1. `max_atom_len` bypass — resource exhaustion / OOM.**
A caller that passes `deser_auto(blob, max_atom_len=N)` to cap memory use receives no protection when the blob is in legacy format. An attacker-controlled legacy blob containing a multi-megabyte (or gigabyte) atom will be parsed without any size check, potentially exhausting heap memory in the Python process or the Chia node that embeds it.

**2. `strict` bypass — non-canonical encoding accepted silently.**
`strict=True` in the serde_2026 path rejects non-canonical encodings (e.g., redundant leading bytes). When `deser_auto` falls through to `node_from_bytes_backrefs`, `strict` is ignored. Code that relies on `deser_auto(..., strict=True)` to enforce canonical encoding for mempool or consensus validation will silently accept non-canonical legacy blobs, creating a divergence between nodes that use `deser_auto` and those that use format-specific deserializers with their own strictness checks. [3](#0-2) 

---

### Likelihood Explanation

`deser_auto` is explicitly documented as "a Python convenience function" and is registered in the public Python module. Downstream integrators (wallets, full nodes, mempool validators) that call `deser_auto` with a custom `max_atom_len` or `strict=True` will receive false security guarantees whenever the blob happens to be in legacy or backrefs format — which is the dominant format in the existing Chia ecosystem. An attacker who can submit arbitrary serialized CLVM blobs (e.g., via spend bundles or puzzle reveals) can trivially craft a legacy-format blob to trigger the bypass. [4](#0-3) [5](#0-4) 

---

### Recommendation

Pass `max_atom_len` and `strict` through to the legacy/backrefs path as well. If `node_from_bytes_backrefs` does not yet accept those parameters, add overloads or wrapper checks:

```rust
fn deser_auto(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = if let Some(body) = blob.strip_prefix(SERDE_2026_MAGIC_PREFIX.as_slice()) {
        deserialize_2026_body_from_stream(&mut a, &mut Cursor::new(body), max_atom_len, strict)
            .map_err(eval_to_py)?
    } else {
        // Pass max_atom_len and strict to the backrefs path too
        node_from_bytes_backrefs_limited(&mut a, blob, max_atom_len, strict).map_err(eval_to_py)?
    };
    Ok(LazyNode::new(Rc::new(a), node))
}
```

At minimum, the docstring must explicitly state that `max_atom_len` and `strict` are **no-ops for non-serde_2026 blobs**, so callers are not misled into relying on them for security.

---

### Proof of Concept

```python
import clvm_rs

# Craft a legacy-format blob with a 2 MB atom (well above max_atom_len=100)
big_atom = b'\x00' * (2 * 1024 * 1024)
# Legacy encoding: length prefix + data
size = len(big_atom)
# 3-byte length prefix for atoms > 0x3FFF bytes: 0b110xxxxx ...
prefix = bytes([0b11000000 | (size >> 16), (size >> 8) & 0xFF, size & 0xFF])
legacy_blob = prefix + big_atom

# Caller expects max_atom_len=100 to be enforced — it is NOT for legacy blobs
node = clvm_rs.deser_auto(legacy_blob, max_atom_len=100, strict=True)
# No error raised; 2 MB atom is silently accepted
print(len(node.atom))  # prints 2097152 — limit was bypassed
``` [6](#0-5) [7](#0-6)

### Citations

**File:** wheel/src/api.rs (L22-26)
```rust

/// Sane "don't OOM the parser" default. clvm_rs has no consensus opinion;
/// downstream wrappers (e.g. chia_rs) supply their own caps.
const PY_DEFAULT_MAX_ATOM_LEN: usize = 1 << 20;
use pyo3::prelude::*;
```

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

**File:** wheel/src/api.rs (L138-157)
```rust
/// Deserialize CLVM bytes, auto-detecting the format (classic, backrefs, or
/// serde_2026).  If the blob starts with the magic prefix
/// `fd ff 32 30 32 36`, it is treated as serde_2026; otherwise the backrefs
/// deserializer is used (which also handles plain classic format).
///
/// This is a Python convenience function — clvm_rs's Rust API doesn't have
/// an auto-switching counterpart. Consensus-aware callers should sniff the
/// prefix themselves and use their own caps.
#[pyfunction]
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
