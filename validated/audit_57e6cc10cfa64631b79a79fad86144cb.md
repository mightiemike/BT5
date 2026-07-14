### Title
`node_from_bytes` Silently Accepts Trailing Bytes — Consensus Divergence via Partial Deserialization — (`File: src/serde/de.rs`)

### Summary

`node_from_bytes` (and `node_from_bytes_backrefs`) parse exactly one CLVM node from a byte slice and return without verifying that the entire input was consumed. Trailing bytes are silently discarded. Because `run_serialized_chia_program` feeds attacker-controlled program and argument bytes directly through `node_from_bytes`, a crafted input with appended garbage is accepted by clvm_rs while a stricter implementation would reject it, producing a consensus split.

### Finding Description

`node_from_bytes` wraps a `Cursor` around the caller-supplied slice and delegates to `node_from_stream`, which stops as soon as its internal op-stack is empty — i.e., as soon as one complete S-expression has been parsed. No check is made that `buffer.position() == b.len()`.

```rust
// src/serde/de.rs  lines 44-47
pub fn node_from_bytes(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream(allocator, &mut buffer)   // cursor position never validated
}
```

`node_from_stream` exits the `while` loop the moment `ops` is empty, regardless of how many bytes remain unread in the cursor:

```rust
// src/serde/de.rs  lines 21-41
while let Some(op) = ops.pop() { ... }
Ok(values.pop().unwrap())          // trailing bytes never checked
```

The same defect is present in `node_from_bytes_backrefs` (`src/serde/de_br.rs`, lines 114-117).

Both functions are called unconditionally from the primary Python-facing entry point:

```rust
// wheel/src/api.rs  lines 55-56
let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
let args    = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
```

Concrete trigger: passing `b"\x80\xff"` (nil atom followed by one garbage byte) to `node_from_bytes` returns `Ok(NodePtr::NIL)` — the `\xff` byte is never read and no error is raised.

The repository already exposes `is_canonical_serialization` and `serialized_length_from_bytes` (re-exported from `src/serde/tools.rs` via `src/serde/mod.rs` line 40) as separate utilities, confirming that trailing-byte validation is a known concern — yet it is not enforced in the hot deserialization path.

Notably, the newer `serde_2026` deserializer exposes an explicit `strict` parameter (`wheel/src/api.rs` line 122) that rejects trailing bytes when `strict=true`, demonstrating that the project is aware of the distinction and has chosen to enforce it in the new format but not in the legacy one.

### Impact Explanation

Any full node or wallet that uses clvm_rs to validate a spend bundle will accept a program or argument blob that has trailing bytes appended. If the reference Python implementation (or any other consensus peer) rejects such a blob, the two nodes reach different conclusions about the validity of the same transaction, producing a **consensus split**. An attacker who can submit transactions to the mempool can craft a spend bundle whose program bytes are a valid CLVM program followed by one or more garbage bytes; clvm_rs executes it successfully while a stricter node rejects it, potentially allowing double-spends or chain forks.

### Likelihood Explanation

The entry path is fully attacker-controlled: `run_serialized_chia_program` accepts raw bytes from Python callers (wallets, full nodes, light clients) with no pre-validation. Crafting a valid CLVM serialization with appended garbage requires no special privileges — any user who can submit a transaction can trigger this path. The only uncertainty is whether the Python reference implementation also silently ignores trailing bytes; if it does, the divergence window narrows to third-party implementations. Given that `is_canonical_serialization` exists precisely to detect non-minimal encodings and trailing data, and that `serde_2026` enforces strictness explicitly, the legacy path's permissiveness is an unintentional omission rather than a deliberate design choice.

### Recommendation

After `node_from_stream` returns, verify that the cursor has reached the end of the buffer:

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

Apply the same fix to `node_from_bytes_backrefs` in `src/serde/de_br.rs`. Alternatively, call `serialized_length_from_bytes` before parsing and reject inputs whose declared length does not equal the slice length.

### Proof of Concept

```rust
use clvmr::allocator::Allocator;
use clvmr::serde::node_from_bytes;

fn main() {
    let mut a = Allocator::new();
    // 0x80 = nil; 0xff = CONS_BOX_MARKER (garbage trailing byte)
    let blob: &[u8] = &[0x80, 0xff];
    // Returns Ok(NIL) — trailing 0xff is silently discarded
    let node = node_from_bytes(&mut a, blob).expect("should have been rejected");
    println!("Parsed node: {:?} (trailing byte ignored)", node);
}
```

The call succeeds and returns `NodePtr::NIL`. A strict implementation would return an error, causing the two nodes to disagree on whether the spend bundle is valid. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** wheel/src/api.rs (L122-123)
```rust
#[pyo3(signature = (blob, *, max_atom_len=PY_DEFAULT_MAX_ATOM_LEN, strict=true))]
fn deser_2026(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode> {
```

**File:** src/serde/mod.rs (L39-42)
```rust
pub use tools::{
    is_canonical_serialization, serialized_length_from_bytes, serialized_length_from_bytes_trusted,
    tree_hash_from_stream,
};
```
