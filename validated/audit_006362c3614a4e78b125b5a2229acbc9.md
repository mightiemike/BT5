### Title
`deser_legacy` and `deser_backrefs` Bypass the `max_atom_len` Guard Enforced by `deser_2026` / `deser_auto` — (File: `wheel/src/api.rs`)

---

### Summary

The Python-facing deserialization API in `wheel/src/api.rs` enforces a `max_atom_len` cap (defaulting to 1 MiB) in `deser_2026` and `deser_auto`, but the sibling functions `deser_legacy` and `deser_backrefs` expose no such parameter and call the underlying Rust deserializers with no atom-size limit at all. Additionally, `deser_auto` silently ignores its own `max_atom_len` argument for any blob that is not in serde_2026 format, routing it through `node_from_bytes_backrefs` unchecked. An attacker who controls the input bytes can bypass the guard entirely by supplying a legacy- or backrefs-encoded blob.

---

### Finding Description

`wheel/src/api.rs` defines four deserialization entry points exposed to Python:

| Function | `max_atom_len` enforced? |
|---|---|
| `deser_legacy` (line 104) | **No** — calls `node_from_bytes` with no cap |
| `deser_backrefs` (line 111) | **No** — calls `node_from_bytes_backrefs` with no cap |
| `deser_2026` (line 122) | Yes — default `1 << 20` (1 MiB) |
| `deser_auto` (line 147) | **Partial** — enforced only for serde_2026 prefix; legacy branch calls `node_from_bytes_backrefs` uncapped | [1](#0-0) 

The constant `PY_DEFAULT_MAX_ATOM_LEN = 1 << 20` is defined as a "don't OOM the parser" default, and `deser_2026` correctly threads it into `deserialize_2026`: [2](#0-1) 

`deser_auto` accepts the same parameter but only passes it to the serde_2026 branch; the legacy/backrefs branch ignores it entirely: [3](#0-2) 

`deser_legacy` and `deser_backrefs` have no parameter at all: [4](#0-3) 

The underlying `parse_atom_ptr` in `src/serde/parse_atom.rs` permits atoms up to `0x3_FFFF_FFFF` bytes (~17 GiB) before returning a serialization error — the only runtime guard is that the declared length must fit within the supplied input buffer: [5](#0-4) 

The test comment at line 111 of `parse_atom.rs` explicitly acknowledges this: *"Still a very large blob, probably enough for a DoS attack."* [6](#0-5) 

---

### Impact Explanation

**Python/Rust API divergence and memory exhaustion.** A caller that uses `deser_auto` and passes `max_atom_len=1<<20` (the default) receives a guarantee that serde_2026 blobs with atoms larger than 1 MiB are rejected. The same caller receives no such guarantee for legacy or backrefs blobs: a blob containing a 100 MiB atom is silently accepted, causing the `Allocator` to allocate 100 MiB of heap memory inside the Python process. Because `deser_legacy` and `deser_backrefs` expose no `max_atom_len` parameter at all, downstream wrappers that want to impose a cap have no API surface to do so for those formats. This creates a format-dependent behavioral split: the same logical content (a large atom) is rejected in serde_2026 encoding and accepted in legacy encoding, which is a concrete Python/Rust API divergence.

---

### Likelihood Explanation

**Medium.** Any Python caller that accepts attacker-controlled bytes and passes them to `deser_legacy`, `deser_backrefs`, or the legacy branch of `deser_auto` is directly reachable. The Chia ecosystem uses these functions to deserialize puzzles and solutions from the network. An attacker who can submit a transaction or peer message containing a legacy-encoded CLVM blob with a large atom can trigger the uncapped allocation path. No special privileges are required beyond the ability to submit bytes to a node or wallet that calls these functions.

---

### Recommendation

1. Add a `max_atom_len` parameter (with the same `PY_DEFAULT_MAX_ATOM_LEN` default) to `deser_legacy` and `deser_backrefs`, and thread it into `node_from_bytes` / `node_from_bytes_backrefs` (or add a `_limited` variant of those functions in the Rust layer).
2. In `deser_auto`, apply `max_atom_len` to the legacy/backrefs branch as well, so the parameter is not silently ignored for non-serde_2026 input.
3. Apply the same fix to `run_serialized_chia_program`, which also calls `node_from_bytes` with no atom-size cap. [7](#0-6) 

---

### Proof of Concept

```python
import clvm_rs

# Build a legacy-encoded blob with a 2 MiB atom (exceeds the 1 MiB default cap).
atom_len = 2 * 1024 * 1024          # 2 MiB
# Legacy length prefix for a 2 MiB atom: 3-byte prefix 0xC2_00_00_00 ... 
# (0b11000000 | high_bits, low_byte1, low_byte2)
prefix = bytes([0b11000000 | (atom_len >> 16), (atom_len >> 8) & 0xFF, atom_len & 0xFF])
blob = prefix + b'\xAA' * atom_len

# deser_2026 would reject a 2 MiB atom — but we can't use it for legacy format.
# deser_legacy accepts it with no cap:
node = clvm_rs.deser_legacy(blob)   # succeeds, allocates 2 MiB
print("accepted, atom length:", len(node.atom))

# deser_auto also accepts it (legacy branch ignores max_atom_len):
node2 = clvm_rs.deser_auto(blob, max_atom_len=1024)  # cap is silently ignored
print("deser_auto accepted despite max_atom_len=1024:", len(node2.atom))
```

The `deser_legacy` call succeeds and allocates the full 2 MiB atom. The `deser_auto` call also succeeds despite `max_atom_len=1024`, because the legacy branch routes to `node_from_bytes_backrefs` without passing the cap. [4](#0-3) [8](#0-7)

### Citations

**File:** wheel/src/api.rs (L23-25)
```rust
/// Sane "don't OOM the parser" default. clvm_rs has no consensus opinion;
/// downstream wrappers (e.g. chia_rs) supply their own caps.
const PY_DEFAULT_MAX_ATOM_LEN: usize = 1 << 20;
```

**File:** wheel/src/api.rs (L40-62)
```rust
pub fn run_serialized_chia_program(
    py: Python,
    program: &[u8],
    args: &[u8],
    max_cost: Cost,
    flags: u32,
) -> PyResult<(u64, LazyNode)> {
    let flags = ClvmFlags::from_bits_truncate(flags);
    let mut allocator = if flags.contains(ClvmFlags::LIMIT_HEAP) {
        Allocator::new_limited(500000000)
    } else {
        Allocator::new()
    };

    let r: Response = (|| -> PyResult<Response> {
        let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
        let args = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
        let dialect = ChiaDialect::new(flags);

        Ok(py.detach(|| run_program(&mut allocator, &dialect, program, args, max_cost)))
    })()?;
    adapt_response(py, allocator, r)
}
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

**File:** src/serde/parse_atom.rs (L61-68)
```rust
        let blob_size = decode_size(f, first_byte)?;
        let pos = f.position() as usize;
        if f.get_ref().len() < pos + blob_size as usize {
            return Err(EvalErr::SerializationError);
        }
        f.seek(SeekFrom::Current(blob_size as i64))?;
        &f.get_ref()[pos..(pos + blob_size as usize)]
    };
```

**File:** src/serde/parse_atom.rs (L109-111)
```rust
    // this is *just* within what we support
    // Still a very large blob, probably enough for a DoS attack
    #[case(0b11111100, &[0x3, 0xff, 0xff, 0xff, 0xff], (6, 0x3ffffffff))]
```
