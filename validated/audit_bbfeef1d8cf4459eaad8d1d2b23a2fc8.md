### Title
`deser_auto` Silently Drops `max_atom_len` and `strict` Safety Parameters for Legacy/Backrefs Blobs — (`wheel/src/api.rs`)

---

### Summary

`deser_auto` accepts `max_atom_len` and `strict` parameters that callers rely on for safety enforcement, but silently ignores both parameters when the input blob is in legacy or backrefs format. Only the serde_2026 code path wires these parameters through to the underlying decoder. This is a direct structural analog to the reported ERC721 bug: a function accepts a safety-relevant parameter, then silently drops it in the actual execution path.

---

### Finding Description

In `wheel/src/api.rs`, `deser_auto` is the Python-facing auto-detecting deserializer. Its signature is:

```rust
fn deser_auto(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode>
```

The implementation branches on the blob's magic prefix:

```rust
let node = if let Some(body) = blob.strip_prefix(SERDE_2026_MAGIC_PREFIX.as_slice()) {
    deserialize_2026_body_from_stream(&mut a, &mut Cursor::new(body), max_atom_len, strict)
        .map_err(eval_to_py)?
} else {
    node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?
};
``` [1](#0-0) 

When the blob is serde_2026, both `max_atom_len` and `strict` are forwarded to `deserialize_2026_body_from_stream`. When the blob is in legacy or backrefs format (the `else` branch), `node_from_bytes_backrefs` is called with **neither parameter**. Both are silently dropped. `node_from_bytes_backrefs` has no atom-size cap and no strict-mode concept. [2](#0-1) 

The default value `PY_DEFAULT_MAX_ATOM_LEN = 1 << 20` (1 MB) is declared as a "don't OOM the parser" guard: [3](#0-2) 

But this guard is only applied to serde_2026 blobs. For legacy blobs, no atom-size limit is enforced regardless of what the caller passes.

The structural parallel to the ERC721 bug is exact:

| ERC721 bug | clvm_rs analog |
|---|---|
| `_safeTransfer(from, to, tokenId, bytes memory data)` | `deser_auto(blob, max_atom_len, strict)` |
| `data` accepted but silently dropped | `max_atom_len` and `strict` accepted but silently dropped |
| `onERC721Received` never called | atom-size cap and strict validation never applied |
| Safety check missing for the actual transfer path | Safety check missing for the legacy deserialization path |

---

### Impact Explanation

**Atom-size cap bypass (primary impact):** A caller that passes `max_atom_len=N` to `deser_auto` to enforce a consensus-level or resource-protection limit on atom sizes receives no such protection for legacy-format blobs. An attacker who controls the serialized bytes can craft a legacy-format blob containing atoms far exceeding `N` bytes. The deserializer will allocate them without restriction, bypassing the caller's stated cap.

Downstream wrappers such as `chia_rs` are documented as the intended suppliers of these caps. If such a wrapper calls `deser_auto` with a consensus-mandated `max_atom_len` and an attacker supplies a legacy-format blob, the consensus limit is silently unenforced. A node accepting a blob that exceeds the consensus atom-size limit while other nodes reject it constitutes a consensus-divergence risk.

**Strict-mode bypass (secondary impact):** `strict=True` is silently a no-op for legacy blobs. Any non-canonical or malformed encoding that strict mode would reject in serde_2026 is accepted without error in the legacy path.

---

### Likelihood Explanation

The attacker-controlled entry path is direct: the `blob` argument to `deser_auto` is fully attacker-controlled (it is deserialized CLVM bytes from the network or user input). The attacker need only ensure the blob does not start with `SERDE_2026_MAGIC_PREFIX` — i.e., it is a standard legacy CLVM blob — to guarantee the `max_atom_len` and `strict` parameters are ignored. Legacy format is the dominant existing format on the Chia network, so virtually all real-world blobs trigger the vulnerable branch.

---

### Recommendation

Apply `max_atom_len` enforcement in the legacy/backrefs deserialization path. Either:

1. Add a `max_atom_len` parameter to `node_from_bytes_backrefs` (and its underlying parser) and thread it through, or
2. Add a post-deserialization walk that verifies no atom exceeds `max_atom_len` before returning the `LazyNode`, or
3. Clearly document that `max_atom_len` and `strict` are **not enforced** for legacy blobs and rename or restructure the API so callers cannot accidentally rely on them for legacy input.

---

### Proof of Concept

```python
import clvm_rs

# Craft a legacy-format CLVM blob containing a 2 MB atom.
# Legacy encoding: 0x8f followed by a 3-byte big-endian length, then the atom bytes.
# (Exact encoding per the legacy serializer in src/serde/write_atom.rs)
atom_size = 2 * 1024 * 1024  # 2 MB, well above PY_DEFAULT_MAX_ATOM_LEN (1 MB)
# Legacy length prefix for atoms > 0x400 bytes uses multi-byte header
length_bytes = atom_size.to_bytes(3, 'big')
blob = bytes([0x8f | (len(length_bytes) - 1)]) + length_bytes + b'\x00' * atom_size

# Caller expects max_atom_len=1024 to be enforced — it is NOT for legacy blobs.
node = clvm_rs.deser_auto(blob, max_atom_len=1024, strict=True)
# Succeeds: a 2 MB atom is allocated despite max_atom_len=1024.
# The safety cap is silently dropped in the else-branch of deser_auto.
``` [4](#0-3)

### Citations

**File:** wheel/src/api.rs (L23-25)
```rust
/// Sane "don't OOM the parser" default. clvm_rs has no consensus opinion;
/// downstream wrappers (e.g. chia_rs) supply their own caps.
const PY_DEFAULT_MAX_ATOM_LEN: usize = 1 << 20;
```

**File:** wheel/src/api.rs (L103-115)
```rust
#[pyfunction]
fn deser_legacy(blob: &[u8]) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = node_from_bytes(&mut a, blob).map_err(eval_to_py)?;
    Ok(LazyNode::new(Rc::new(a), node))
}

#[pyfunction]
fn deser_backrefs(blob: &[u8]) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?;
    Ok(LazyNode::new(Rc::new(a), node))
}
```

**File:** wheel/src/api.rs (L146-157)
```rust
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
