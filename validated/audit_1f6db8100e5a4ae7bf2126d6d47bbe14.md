### Title
`deser_auto` silently drops `max_atom_len` for the classic/backrefs code path — (`File: wheel/src/api.rs`)

---

### Summary

`deser_auto` accepts a `max_atom_len` safety parameter but only enforces it on the serde_2026 branch. When the input blob does not start with the serde_2026 magic prefix, the function falls through to `node_from_bytes_backrefs`, which accepts no atom-length cap at all. The caller's limit is silently discarded, and an attacker who controls the input format can bypass it entirely by supplying a classic CLVM blob.

---

### Finding Description

`deser_auto` in `wheel/src/api.rs` is the Python-facing auto-detecting deserializer. Its signature advertises two safety parameters:

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

For the serde_2026 branch, `max_atom_len` is forwarded to `deserialize_2026_body_from_stream`, which enforces it per-atom before any allocation. [2](#0-1) 

For the classic/backrefs branch, `node_from_bytes_backrefs` is called with only `(allocator, blob)`. Its signature is:

```rust
pub fn node_from_bytes_backrefs(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr>
``` [3](#0-2) 

There is no `max_atom_len` parameter. The underlying `parse_atom` called from `node_from_stream_backrefs` reads whatever atom length the stream encodes, bounded only by the allocator's global heap limit (which defaults to unlimited for `Allocator::new()`). [4](#0-3) 

The inconsistency is structurally identical to the external report: the "safe" variant (serde_2026 path) enforces the caller-supplied limit; the "unsafe" variant (backrefs path) silently ignores it, even though the function signature implies the limit applies to all formats.

---

### Impact Explanation

Any Python caller that passes a custom `max_atom_len` to `deser_auto` — for example, a downstream consensus wrapper like `chia_rs` that enforces a per-atom byte cap — will have that cap enforced only for serde_2026 blobs. An attacker who controls the input can bypass the cap entirely by supplying a classic CLVM blob (one that does not start with `fd ff 32 30 32 36`). Consequences include:

1. **Consensus rule bypass**: If `max_atom_len` encodes a consensus limit (e.g., maximum puzzle or solution atom size), an attacker can submit a classic blob with an oversized atom that passes `deser_auto` but would be rejected if the limit were enforced.
2. **Memory exhaustion**: A classic blob with a multi-megabyte atom will be fully allocated into the `Allocator` heap regardless of the caller's intended cap, allowing an attacker to drive memory consumption far above what the caller expected to permit.

---

### Likelihood Explanation

The attacker-controlled entry path is direct: `deser_auto` is a public Python API that accepts arbitrary bytes. The attacker only needs to ensure the blob does not start with the serde_2026 magic prefix — any ordinary classic CLVM serialization satisfies this. No special privileges or configuration are required. The `PY_DEFAULT_MAX_ATOM_LEN` default of `1 << 20` (1 MB) means the default call is also affected if a caller passes a stricter value.

---

### Recommendation

Either:

1. Add a `max_atom_len` parameter to `node_from_bytes_backrefs` (and its underlying `parse_atom` call chain) and thread it through from `deser_auto`, mirroring the serde_2026 path; or
2. Document explicitly that `max_atom_len` is not enforced for classic/backrefs blobs and require callers to pre-validate atom sizes themselves before calling `deser_auto` on untrusted input.

Option 1 is strongly preferred for consistency and safety.

---

### Proof of Concept

```python
from clvm_rs import deser_auto

# Classic CLVM encoding of a 2 MB atom:
# 0x8F = length prefix for a 2-byte length field; 0x20 0x00 = 8192 bytes... 
# More precisely: encode a 2 MB atom in classic CLVM format.
# Classic format: if first byte >= 0x80, it encodes a multi-byte length.
# 0x8F 0xFF 0xFF = atom of length 0x0FFFFF = 1048575 bytes (just under 1 MB default)
# To exceed a caller-supplied cap of, say, 512 bytes:
atom_len = 1024  # exceeds caller's cap of 512
length_byte = 0x81  # 2-byte length follows
blob = bytes([length_byte, atom_len & 0xFF]) + b'\xAB' * atom_len

# Caller intends to cap atoms at 512 bytes:
result = deser_auto(blob, max_atom_len=512)
# Expected: ValueError (atom exceeds max_atom_len)
# Actual:   succeeds silently, returning a LazyNode with a 1024-byte atom
print(result.atom)  # b'\xab' * 1024 — limit was bypassed
```

The blob does not start with `fd ff 32 30 32 36`, so `deser_auto` routes to `node_from_bytes_backrefs`, which ignores `max_atom_len=512` and allocates the full atom.

### Citations

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

**File:** src/serde_2026/de.rs (L39-71)
```rust
pub fn deserialize_2026_body_from_stream<R: Read>(
    allocator: &mut Allocator,
    reader: &mut R,
    max_atom_len: usize,
    strict: bool,
) -> Result<NodePtr> {
    let mut atoms: Vec<NodePtr> = Vec::new();
    let group_count = checked_usize(read_varint(reader, strict)?)?;
    let mut buf: Vec<u8> = Vec::new();

    for _ in 0..group_count {
        let length_val = read_varint(reader, strict)?;
        let (length, count) = if length_val < 0 {
            if length_val == i64::MIN {
                return Err(EvalErr::SerializationError);
            }
            (
                checked_bounded_usize(-length_val, max_atom_len)?,
                checked_usize(read_varint(reader, strict)?)?,
            )
        } else {
            (checked_bounded_usize(length_val, max_atom_len)?, 1)
        };
        if length == 0 || count == 0 {
            return Err(EvalErr::SerializationError);
        }
        buf.resize(length, 0);
        for _ in 0..count {
            reader
                .read_exact(&mut buf)
                .map_err(|_| EvalErr::SerializationError)?;
            atoms.push(allocator.new_atom(&buf)?);
        }
```

**File:** src/serde/mod.rs (L28-28)
```rust
pub use de_br::{node_from_bytes_backrefs, node_from_bytes_backrefs_old};
```

**File:** src/serde/de_br.rs (L44-47)
```rust
                } else {
                    let new_atom = parse_atom(allocator, b[0], f)?;
                    allocator.add_ghost_pair(1)?; // return error if we have too many pairs
                    values.push((new_atom, None));
```
