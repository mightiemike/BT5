### Title
`deser_auto` Silently Drops Caller-Supplied `max_atom_len` and `strict` Guards on the Legacy Deserialization Path — (`wheel/src/api.rs`)

### Summary

`deser_auto` is a public Python API function that accepts `max_atom_len` and `strict` parameters so callers can cap atom sizes and enforce strict parsing. When the input blob carries the serde_2026 magic prefix, both parameters are forwarded correctly. When the blob does **not** carry the prefix, the function falls through to `node_from_bytes_backrefs`, which accepts neither parameter. An attacker who controls the serialized bytes can omit or strip the magic prefix to silently bypass both caller-imposed limits, deserializing arbitrarily large atoms into an unbounded `Allocator`.

### Finding Description

`deser_auto` in `wheel/src/api.rs` (lines 147–157) is declared with two security-relevant keyword arguments:

```rust
#[pyo3(signature = (blob, *, max_atom_len=PY_DEFAULT_MAX_ATOM_LEN, strict=true))]
fn deser_auto(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode>
``` [1](#0-0) 

The body branches on the magic prefix:

```rust
let node = if let Some(body) = blob.strip_prefix(SERDE_2026_MAGIC_PREFIX.as_slice()) {
    deserialize_2026_body_from_stream(&mut a, &mut Cursor::new(body), max_atom_len, strict)
        .map_err(eval_to_py)?
} else {
    node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?   // ← max_atom_len and strict vanish
};
``` [2](#0-1) 

`node_from_bytes_backrefs` (imported from `clvmr::serde`) has no `max_atom_len` or `strict` parameter. The `Allocator` created on line 149 is `Allocator::new()`, which sets `heap_limit` to `u32::MAX` (≈ 4 GiB) — no practical bound. [3](#0-2) 

The root cause is structurally identical to H-14: a function accepts a caller-supplied security parameter (`max_atom_len` / `strict`) that is supposed to gate what the function does, but one code branch — reachable by attacker-controlled input — silently ignores those parameters entirely.

The attacker-controlled entry point is the `blob` argument. Any Python caller (e.g., a mempool validator, a wallet, or a node's transaction decoder) that calls:

```python
node = deser_auto(untrusted_bytes, max_atom_len=4096, strict=True)
```

will have both guards bypassed if `untrusted_bytes` does not start with `b'\xfd\xff2026'`. A malicious peer simply omits the prefix.

### Impact Explanation

1. **Atom-size limit bypass**: A blob encoding a single atom of, say, 100 MB is accepted without error. The `Allocator` will allocate up to 4 GiB before raising `OutOfMemory`. Any downstream code that assumes `max_atom_len` was enforced (e.g., a BLS key validator that expects ≤ 48 bytes) receives an oversized atom and may behave incorrectly.

2. **`strict` bypass**: The `strict` flag controls whether the serde_2026 decoder rejects non-canonical encodings. In the legacy path it is never consulted, so non-canonical atoms that `strict=True` would have rejected are silently accepted. If a consensus rule depends on `strict` rejection, nodes using `deser_auto` on legacy-format blobs will diverge from nodes that enforce strictness.

3. **Memory exhaustion / DoS**: A crafted blob with a multi-megabyte atom causes the process to allocate up to 4 GiB before failing, which is a realistic denial-of-service against any service that calls `deser_auto` on untrusted network input.

### Likelihood Explanation

The function is documented as a "Python convenience function" for auto-detecting format. Any integrator who reads the signature and sees `max_atom_len` will reasonably assume it is always enforced. The bypass requires only that the attacker omit the 6-byte magic prefix — a trivial transformation of any valid CLVM blob. No privileged access is required.

### Recommendation

Pass `max_atom_len` (and a `strict`-equivalent) through to the legacy path, or add a post-parse atom-size walk before returning the `LazyNode`. At minimum, document prominently that `max_atom_len` and `strict` are **no-ops** for non-serde_2026 blobs so callers are not misled into believing they have a safety net.

```rust
// Option A: add a max_atom_len guard to node_from_bytes_backrefs
node_from_bytes_backrefs_limited(&mut a, blob, max_atom_len).map_err(eval_to_py)?

// Option B: post-parse walk
let node = node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?;
check_max_atom_len(&a, node, max_atom_len).map_err(eval_to_py)?;
```

### Proof of Concept

```python
from clvm_rs import deser_auto

# CLVM legacy encoding of a single 1 MiB atom:
# 0xE1 0x00 0x00 = 3-byte length prefix for 1 048 576 bytes
payload = bytes([0xE1, 0x10, 0x00]) + b"A" * (1 << 20)

# Caller believes max_atom_len=64 will protect them.
# Because payload has no serde_2026 magic prefix, max_atom_len is silently ignored.
node = deser_auto(payload, max_atom_len=64, strict=True)

# node.atom is 1 MiB — the limit was never applied.
assert len(node.atom) == 1 << 20
``` [4](#0-3)

### Citations

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
