### Title
`node_from_bytes` Silently Ignores Trailing Bytes After Valid CLVM Encoding — (`File: src/serde/de.rs`)

### Summary
`node_from_bytes` deserializes attacker-controlled CLVM bytes using an internal `Cursor`, but never checks whether the cursor was fully consumed after parsing. Trailing garbage bytes are silently discarded. This is a direct analog to the stale-price-feed class: a validity indicator (the cursor's remaining-bytes count) is returned by the underlying mechanism but never read, so non-canonical input is accepted as if it were valid.

### Finding Description
`node_from_bytes` is the primary public entry point for deserializing raw CLVM bytes into an `Allocator`-backed tree:

```rust
// src/serde/de.rs  lines 44-47
pub fn node_from_bytes(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream(allocator, &mut buffer)
}
```

`node_from_stream` reads exactly as many bytes as the encoded CLVM tree requires and then returns. The `Cursor`'s internal position after the call encodes whether all of `b` was consumed — i.e., whether the input is canonical. That position is never inspected; the function returns immediately with the parsed `NodePtr`.

A correct implementation would add:

```rust
if buffer.position() != b.len() as u64 {
    return Err(/* non-canonical / trailing bytes */);
}
```

The missing check is structurally identical to the Oracle bug: `latestRoundData()` returns `(roundId, answer, startedAt, updatedAt, answeredInRound)` and the caller uses only `answer`, ignoring `updatedAt`. Here, `node_from_stream` implicitly "returns" the cursor's remaining-bytes count, and the caller ignores it entirely. [1](#0-0) 

### Impact Explanation
Any caller that passes attacker-controlled bytes to `node_from_bytes` will silently accept a non-canonical encoding. Concretely:

1. **Consensus divergence** — if a stricter implementation (e.g., the Python reference CLVM or a future Rust version) rejects inputs with trailing bytes, the two implementations will disagree on whether a given serialized program is valid. A transaction whose program blob contains trailing bytes would be accepted by one node and rejected by another, splitting consensus.
2. **Non-canonical program identity** — programs in Chia are identified by their tree hash (sha256tree of the parsed structure). Two byte strings that differ only in trailing garbage parse to the same tree and therefore share the same tree hash. An attacker can submit multiple distinct byte strings that all "are" the same program, potentially confusing caches, mempool deduplication, or audit tooling that operates on raw bytes.
3. **Downstream API misuse** — the Python wheel (`wheel/`) calls into the same Rust core. Python callers that pass user-supplied bytes to `node_from_bytes` (directly or via `run_serialized_chia_program`) inherit the silent-acceptance behavior.

### Likelihood Explanation
The entry path is direct and requires no special privilege. Any party that can submit a serialized CLVM program to a Chia full node (i.e., any transaction sender) can append arbitrary trailing bytes to the program blob. The fast path in `node_from_stream` terminates as soon as the tree is complete; it does not scan to end-of-input. The bug is therefore reachable on every call to `node_from_bytes` with a non-canonical input.

### Recommendation
After calling `node_from_stream`, assert that the cursor is exhausted:

```rust
pub fn node_from_bytes(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    let node = node_from_stream(allocator, &mut buffer)?;
    if buffer.position() != b.len() as u64 {
        return Err(EvalErr::InvalidInput(
            NodePtr::NIL,
            "trailing bytes after CLVM object".to_string(),
        ).into());
    }
    Ok(node)
}
```

Apply the same guard to any other public deserialization entry points (e.g., `node_from_bytes_backrefs` if present) that wrap `node_from_stream` without consuming the cursor.

### Proof of Concept
```
Input bytes: FF 01 80 DE AD BE EF
             ^^^^^^^^^^^  valid (1 . ())
                          ^^^^^^^^^^^^ trailing garbage
```

`node_from_bytes` returns `Ok(NodePtr)` representing `(1 . ())`. The four trailing bytes `DE AD BE EF` are never read. A strict implementation would return an error. The two implementations now disagree on whether this byte string is a valid program. [2](#0-1)

### Citations

**File:** src/serde/de.rs (L16-47)
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

pub fn node_from_bytes(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream(allocator, &mut buffer)
}
```
