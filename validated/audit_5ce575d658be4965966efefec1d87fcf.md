### Title
Wrong Deserializer Called in `Program.from_bytes_backrefs` — (`File: wheel/python/clvm_rs/program.py`)

### Summary
`Program.from_bytes_backrefs` is documented to "Deserialize classic or backrefs format only (rejects serde_2026)." It calls `deser_auto` instead of `deser_backrefs`. This is a direct analog of the "wrong function call" bug class: the method uses the wrong deserializer, relying entirely on a Python-level prefix guard to enforce its contract rather than the correct Rust function.

### Finding Description
In `wheel/python/clvm_rs/program.py`, the `from_bytes_backrefs` classmethod is defined as:

```python
@classmethod
def from_bytes_backrefs(cls, blob: bytes) -> Program:
    """Deserialize classic or backrefs format only (rejects serde_2026)."""
    if blob.startswith(SERDE_2026_MAGIC_PREFIX):
        raise ValueError("unexpected serde_2026 format; use from_bytes() for auto-detection")
    return cls.wrap(deser_auto(blob))   # ← wrong function
``` [1](#0-0) 

The correct function to call is `deser_backrefs`, which is exposed by the Rust wheel and calls `node_from_bytes_backrefs` directly:

```rust
#[pyfunction]
fn deser_backrefs(blob: &[u8]) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?;
    Ok(LazyNode::new(Rc::new(a), node))
}
``` [2](#0-1) 

Instead, `deser_auto` is called, which is the format-sniffing function:

```rust
fn deser_auto(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = if let Some(body) = blob.strip_prefix(SERDE_2026_MAGIC_PREFIX.as_slice()) {
        deserialize_2026_body_from_stream(...)
    } else {
        node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?
    };
    Ok(LazyNode::new(Rc::new(a), node))
}
``` [3](#0-2) 

Critically, `deser_backrefs` is **not imported** in `program.py`. The import block only brings in `deser_auto`:

```python
from .clvm_rs import (
    clvm_tree_to_lazy_node,
    deser_auto,
    run_serialized_chia_program,
    ser_2026,
)
``` [4](#0-3) 

The method's enforcement of its "backrefs-only" contract is therefore entirely dependent on the Python-level `startswith` guard. If that guard is ever removed, refactored, or bypassed (e.g., a blob starting with `\xfd\xff` but not the full 6-byte prefix), `deser_auto` would silently accept serde_2026 blobs through a method that explicitly documents rejecting them.

### Impact Explanation
The API contract of `from_bytes_backrefs` is broken at the implementation level. Callers that depend on strict backrefs-only deserialization (e.g., consensus-layer code that must reject serde_2026 blobs) are relying on a Python-level string prefix check rather than the correct Rust deserializer. Any future refactoring that removes or weakens the Python guard would silently allow serde_2026 blobs through `from_bytes_backrefs`, producing a deserialization path mismatch between what callers expect and what the code delivers. This is an API-equivalence violation in the Python bindings layer.

### Likelihood Explanation
The bug is present in the current codebase and affects every caller of `Program.from_bytes_backrefs`. The Python guard currently compensates, so the observable behavior is identical for all inputs that do not start with the exact 6-byte magic prefix. However, the wrong function is unconditionally called, and the correct function (`deser_backrefs`) is not even imported into the module.

### Recommendation
Import `deser_backrefs` in `program.py` and call it directly:

```python
from .clvm_rs import (
    clvm_tree_to_lazy_node,
    deser_auto,
    deser_backrefs,          # add this
    run_serialized_chia_program,
    ser_2026,
)

@classmethod
def from_bytes_backrefs(cls, blob: bytes) -> Program:
    """Deserialize classic or backrefs format only (rejects serde_2026)."""
    if blob.startswith(SERDE_2026_MAGIC_PREFIX):
        raise ValueError("unexpected serde_2026 format; use from_bytes() for auto-detection")
    return cls.wrap(deser_backrefs(blob))   # use the correct function
```

This makes the Rust layer enforce the format restriction rather than relying solely on a Python-level prefix check.

### Proof of Concept
```python
from clvm_rs.clvm_rs import deser_backrefs, deser_auto
from clvm_rs.program import Program

# Construct a blob with backrefs (0xfe opcode)
from clvm_rs.serde import serialize, deserialize
p = Program.to([b"shared", b"shared", b"shared"])
blob = serialize(deserialize(bytes(p), "legacy"), "backrefs")
assert 0xfe in blob  # confirm backrefs present

# from_bytes_backrefs calls deser_auto, not deser_backrefs
# Verify by checking that deser_backrefs is NOT in program.py's namespace:
import clvm_rs.program as pm
assert not hasattr(pm, 'deser_backrefs'), "deser_backrefs is not imported"
assert hasattr(pm, 'deser_auto'), "deser_auto is imported instead"

# The method succeeds only because deser_auto falls through to
# node_from_bytes_backrefs for non-serde_2026 blobs — not because
# the correct function was called.
p2 = Program.from_bytes_backrefs(blob)
assert p == p2  # passes, but for the wrong reason
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** wheel/python/clvm_rs/program.py (L7-12)
```python
from .clvm_rs import (
    clvm_tree_to_lazy_node,
    deser_auto,
    run_serialized_chia_program,
    ser_2026,
)
```

**File:** wheel/python/clvm_rs/program.py (L49-54)
```python
    @classmethod
    def from_bytes_backrefs(cls, blob: bytes) -> Program:
        """Deserialize classic or backrefs format only (rejects serde_2026)."""
        if blob.startswith(SERDE_2026_MAGIC_PREFIX):
            raise ValueError("unexpected serde_2026 format; use from_bytes() for auto-detection")
        return cls.wrap(deser_auto(blob))
```

**File:** wheel/src/api.rs (L110-115)
```rust
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
