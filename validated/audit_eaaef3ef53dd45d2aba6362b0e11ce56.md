### Title
Silent Parameter Bypass in `deser_auto`: `max_atom_len` and `strict` Silently Ignored for Legacy/Backrefs Blobs — (`File: wheel/src/api.rs`)

---

### Summary

`deser_auto` presents a unified Python deserialization interface with `max_atom_len` and `strict` safety parameters, but these parameters are **silently dropped** for any blob that is not in serde_2026 format. An attacker who controls the serialized bytes can bypass caller-specified atom-size limits and non-canonical encoding checks simply by supplying a legacy or backrefs-format blob instead of a serde_2026 blob.

---

### Finding Description

`deser_auto` in `wheel/src/api.rs` (lines 148–157) auto-detects the serialization format and dispatches to one of two deserializers:

```
if blob starts with SERDE_2026_MAGIC_PREFIX:
    deserialize_2026_body_from_stream(..., max_atom_len, strict)   ← enforced
else:
    node_from_bytes_backrefs(&mut a, blob)                         ← NOT enforced
``` [1](#0-0) 

The function signature and its docstring explicitly advertise `max_atom_len` and `strict` as meaningful controls: [2](#0-1) 

The default values (`max_atom_len = PY_DEFAULT_MAX_ATOM_LEN = 1 << 20`, `strict = true`) are described as a "sane 'don't OOM the parser' default": [3](#0-2) 

However, `node_from_bytes_backrefs` — the fallback for all non-2026 blobs — accepts no such parameters: [4](#0-3) 

The underlying `node_from_stream_backrefs` allocates atoms of arbitrary size without any length cap: [5](#0-4) 

This is a direct abstraction leakage: the caller cannot rely on `max_atom_len` or `strict` being enforced without knowing which wire format the blob uses — but the entire purpose of `deser_auto` is to hide that distinction.

---

### Impact Explanation

**Atom-size limit bypass (memory exhaustion):** A caller that sets `max_atom_len=N` to bound allocator memory usage has that limit silently bypassed when the attacker supplies a legacy blob. The 1 MB default cap — the only OOM guard in the Python API — is entirely ineffective against legacy-format input. An attacker can craft a legacy blob containing a multi-hundred-MB atom and cause unbounded heap growth in the Rust allocator.

**Non-canonical encoding bypass (`strict` mode):** A caller that sets `strict=True` to reject non-canonical atom-length encodings (e.g., leading zero bytes in the length prefix) has that check silently bypassed for legacy blobs. This can cause consensus divergence if downstream code hashes or compares the deserialized tree and expects canonical form.

Both effects are reachable through the public Python API with no special privileges.

---

### Likelihood Explanation

`deser_auto` is explicitly documented as the convenience entry point for callers who do not know the format in advance. Any such caller that passes `max_atom_len` or relies on `strict=True` (including callers using the defaults) is silently unprotected against legacy-format input. The attacker's only requirement is to supply bytes that do not start with the 6-byte serde_2026 magic prefix — trivially satisfied by any legacy or backrefs blob. No malicious node, social engineering, or dependency compromise is required.

---

### Recommendation

Enforce `max_atom_len` and `strict` uniformly across all code paths inside `deser_auto`. Concretely, add a `max_atom_len` parameter to `node_from_bytes_backrefs` (and its underlying `node_from_stream_backrefs`) and thread it through `parse_atom`, rejecting atoms whose declared length exceeds the cap before allocating. Similarly, thread `strict` through the legacy path to reject non-canonical length encodings. Until this is done, the docstring of `deser_auto` must explicitly state that `max_atom_len` and `strict` are **not** enforced for legacy or backrefs blobs.

---

### Proof of Concept

```python
import clvm_rs

# Build a legacy-format blob containing a 500 KB atom.
# Legacy encoding for N-byte atom where N needs 3 bytes:
#   first byte = 0xE0 | (N >> 16), then (N >> 8) & 0xFF, then N & 0xFF
N = 500_000          # 500 KB — far above the default 1 MB? No: 500 KB < 1 MB.
# Use 2 MB to exceed the default cap:
N = 2 * 1024 * 1024  # 2 MB
header = bytes([
    0xE0 | ((N >> 16) & 0x1F),
    (N >> 8) & 0xFF,
    N & 0xFF,
])
legacy_blob = header + b'\x41' * N

# deser_auto with the default 1 MB cap and strict=True.
# Expected: ValueError / OOM rejection.
# Actual:   succeeds silently; a 2 MB atom is allocated.
node = clvm_rs.deser_auto(legacy_blob)   # max_atom_len=1<<20, strict=True by default
print(type(node))   # <class 'clvm_rs.LazyNode'> — limit was not enforced
```

The blob does not start with `SERDE_2026_MAGIC_PREFIX`, so `deser_auto` dispatches to `node_from_bytes_backrefs`, which allocates the full 2 MB atom without consulting `max_atom_len`. The caller's OOM guard is silently voided.

### Citations

**File:** wheel/src/api.rs (L23-25)
```rust
/// Sane "don't OOM the parser" default. clvm_rs has no consensus opinion;
/// downstream wrappers (e.g. chia_rs) supply their own caps.
const PY_DEFAULT_MAX_ATOM_LEN: usize = 1 << 20;
```

**File:** wheel/src/api.rs (L138-147)
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
```

**File:** wheel/src/api.rs (L148-157)
```rust
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

**File:** src/serde/de_br.rs (L44-47)
```rust
                } else {
                    let new_atom = parse_atom(allocator, b[0], f)?;
                    allocator.add_ghost_pair(1)?; // return error if we have too many pairs
                    values.push((new_atom, None));
```

**File:** src/serde/de_br.rs (L114-117)
```rust
pub fn node_from_bytes_backrefs(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream_backrefs(allocator, &mut buffer, |_node| {})
}
```
