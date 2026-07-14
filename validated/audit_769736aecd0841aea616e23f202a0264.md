### Title
Unbounded `stack` Vec Growth in `deserialize_2026_body_from_stream` Enables Memory-Amplification DoS — (File: src/serde_2026/de.rs)

### Summary
The `deserialize_2026_body_from_stream` function maintains an internal `stack: Vec<NodePtr>` with no size cap. An attacker can craft a serde_2026 blob containing a large number of push-nil instructions (opcode `0`) to grow the stack proportional to the input size, producing an 8× memory amplification factor. The format documentation explicitly claims "stack-size limits are not needed for DoS protection" and asserts a "maximum input byte budget (default: 10 MiB)" is enforced — but neither claim holds in the actual code.

### Finding Description

In `deserialize_2026_body_from_stream`, the `stack` Vec is initialized at line 81 with `Vec::with_capacity(64)` and has no upper bound:

```rust
let mut stack: Vec<NodePtr> = Vec::with_capacity(64);

for _ in 0..instruction_count {
    let inst = read_varint(reader, strict)?;
    match inst {
        0 => stack.push(nil),          // push nil — no stack-size check
        ...
        n if n >= 2 => {
            stack.push(*atoms.get(ai)...);  // push atom — no stack-size check
        }
        n => {
            stack.push(*pairs.get(pi)...);  // push pair — no stack-size check
        }
    }
}
``` [1](#0-0) 

Every push instruction (opcodes `0`, `N ≥ 2`, `N ≤ -2`) adds one `NodePtr` to `stack` without any size guard. Each `NodePtr` is at minimum 4–8 bytes. Each instruction is encoded as a single varint byte (`0x00` for push-nil). The amplification ratio is therefore at least 4–8× per input byte.

The format documentation at `docs/serde-2026.md` lines 51–56 explicitly states:

> "Separate atom-group, atom-count, instruction-count, **stack-size**, and pair-count limits are not needed for DoS protection: every declared item must consume at least one input byte before it can produce parser work or allocate a CLVM node." [2](#0-1) 

This reasoning is flawed: it conflates *iteration count* with *memory consumption*. Each 1-byte instruction causes 4–8 bytes of `Vec` growth, so the input byte budget does not bound memory usage — it only bounds the number of iterations.

Furthermore, the claimed "maximum input byte budget (default: 10 MiB)" is **not enforced anywhere in the code**. The public `deserialize_2026` function takes a raw `&[u8]` slice with no total-size check:

```rust
pub fn deserialize_2026(
    allocator: &mut Allocator,
    blob: &[u8],
    max_atom_len: usize,
    strict: bool,
) -> Result<NodePtr>
``` [3](#0-2) 

The Python-exposed `deser_2026` API similarly has no total-size limit:

```rust
#[pyfunction]
#[pyo3(signature = (blob, *, max_atom_len=PY_DEFAULT_MAX_ATOM_LEN, strict=true))]
fn deser_2026(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode>
``` [4](#0-3) 

The `max_atom_len` parameter only caps individual atom byte lengths; it does not limit the total input size or the stack depth.

The existing regression tests confirm the project is aware of unbounded-allocation risks (e.g., `deserializer_rejects_unbounded_instruction_count` at line 272), but those tests only cover the case where `instruction_count` is huge with a *tiny* blob. They do not cover the case where `instruction_count` is proportional to a *large* blob, which is the actual attack vector. [5](#0-4) 

### Impact Explanation

An attacker submits a crafted blob to `deser_2026`:

- 6-byte magic prefix
- `group_count = 0` (1 byte: `0x00`)
- `instruction_count = N` (varint, a few bytes)
- N bytes of `0x00` (push-nil instructions)

The `stack` Vec grows to N entries. With N = 10 million (≈10 MB input), the stack consumes ≈80 MB. With N = 100 million (≈100 MB input), ≈800 MB. With N = 1 billion (≈1 GB input), ≈8 GB — causing OOM on most systems. The process crashes or is killed by the OS OOM killer. The `Allocator` pair/atom limits (`MAX_NUM_PAIRS`, `MAX_NUM_ATOMS`) do not apply to the `stack` Vec, which is a plain `Vec<NodePtr>` outside the allocator.

### Likelihood Explanation

The `deser_2026` Python function is a public API that accepts arbitrary caller-supplied bytes. Any service that deserializes attacker-controlled serde_2026 blobs (e.g., a node receiving serialized CLVM programs over the network) is directly reachable. No privileges are required. The attack blob is trivially constructable from the public format specification.

### Recommendation

1. Add an explicit stack-size limit inside `deserialize_2026_body_from_stream`. After each push, check `stack.len()` against a configurable maximum (e.g., `MAX_NUM_PAIRS` or a dedicated constant) and return `Err(EvalErr::SerializationError)` if exceeded.
2. Enforce the documented "maximum input byte budget" as an actual parameter (e.g., `max_bytes: usize`) in `deserialize_2026_body_from_stream`, `deserialize_2026`, and the Python `deser_2026` binding, and reject blobs that exceed it before entering any loop.
3. Correct the format documentation to accurately reflect that the input byte budget alone does not bound memory usage due to the per-NodePtr amplification factor.

### Proof of Concept

```python
from clvm_rs.clvm_rs import deser_2026

# serde_2026 magic prefix
prefix = bytes([0xfd, 0xff, 0x32, 0x30, 0x32, 0x36])

# group_count = 0  (no atoms)
group_count = bytes([0x00])

# instruction_count = 10_000_000, encoded as a multi-byte varint
# Using the 28-bit varint form: 0b1110xxxx xxxxxxxx xxxxxxxx xxxxxxxx
N = 10_000_000  # 0x989680
instruction_count = bytes([
    0b11100000 | (N >> 21),
    (N >> 14) & 0xff,
    (N >> 7)  & 0xff,
    N         & 0x7f,
])

# N bytes of 0x00 = N push-nil instructions (opcode 0)
instructions = bytes(N)

blob = prefix + group_count + instruction_count + instructions
# blob is ~10 MB; deser_2026 will allocate ~80 MB for the stack Vec
# before returning SerializationError (stack.len() != 1)
node = deser_2026(blob)
```

The `stack` Vec grows to 10 million `NodePtr` entries (≈80 MB) before the final `stack.len() != 1` check at line 121 returns an error. Scaling `N` to 1 billion with a 1 GB blob causes ≈8 GB of allocation, triggering OOM. [6](#0-5) [7](#0-6)

### Citations

**File:** src/serde_2026/de.rs (L79-119)
```rust
    let nil = allocator.nil();
    let mut pairs: Vec<NodePtr> = Vec::new();
    let mut stack: Vec<NodePtr> = Vec::with_capacity(64);

    for _ in 0..instruction_count {
        let inst = read_varint(reader, strict)?;
        match inst {
            0 => stack.push(nil),
            1 => {
                if stack.len() < 2 {
                    return Err(EvalErr::SerializationError);
                }
                let right = stack.pop().unwrap();
                let left = stack.pop().unwrap();
                let pair = allocator.new_pair(left, right)?;
                pairs.push(pair);
                stack.push(pair);
            }
            -1 => {
                if stack.len() < 2 {
                    return Err(EvalErr::SerializationError);
                }
                let left = stack.pop().unwrap();
                let right = stack.pop().unwrap();
                let pair = allocator.new_pair(left, right)?;
                pairs.push(pair);
                stack.push(pair);
            }
            n if n >= 2 => {
                let ai = (n - 2) as usize;
                stack.push(*atoms.get(ai).ok_or(EvalErr::SerializationError)?);
            }
            n => {
                let pi = n
                    .checked_neg()
                    .and_then(|x| x.checked_sub(2))
                    .ok_or(EvalErr::SerializationError)? as usize;
                stack.push(*pairs.get(pi).ok_or(EvalErr::SerializationError)?);
            }
        }
    }
```

**File:** src/serde_2026/de.rs (L121-124)
```rust
    if stack.len() != 1 {
        return Err(EvalErr::SerializationError);
    }
    Ok(stack[0])
```

**File:** src/serde_2026/de.rs (L131-138)
```rust
pub fn deserialize_2026(
    allocator: &mut Allocator,
    blob: &[u8],
    max_atom_len: usize,
    strict: bool,
) -> Result<NodePtr> {
    deserialize_2026_from_stream(allocator, &mut Cursor::new(blob), max_atom_len, strict)
}
```

**File:** docs/serde-2026.md (L51-56)
```markdown
Deserializers enforce a configurable maximum atom length (default: 1 MiB) and a
maximum input byte budget (default: 10 MiB). Separate atom-group, atom-count,
instruction-count, stack-size, and pair-count limits are not needed for DoS
protection: every declared item must consume at least one input byte before it
can produce parser work or allocate a CLVM node. The input byte budget therefore
bounds all of those quantities.
```

**File:** wheel/src/api.rs (L122-135)
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
```

**File:** src/serde_2026/tests.rs (L262-293)
```rust
// ---------------------------------------------------------------------------
// Regression test for the unbounded-capacity OOM. A tiny blob
// (under 16 bytes) declares an `instruction_count` near the max representable
// varint (~2^54). Pre-fix, the deserializer pre-allocated `instruction_count
// / 3` `NodePtr`s — a request of about 24 PB — and the process aborted with
// "memory allocation of N bytes failed". Post-fix, the deserializer starts
// with `Vec::new()` and is bounded by the input slice (or caller-supplied
// `Read::take`), so the loop runs out of bytes long before it can drive the
// vector to a pathological size and we return `Err` cleanly.
#[test]
fn deserializer_rejects_unbounded_instruction_count() {
    let mut blob = Vec::new();
    blob.extend_from_slice(&encode_varint(0)); // group_count = 0
    blob.extend_from_slice(&encode_varint(1_i64 << 54)); // instruction_count
    assert!(
        blob.len() < 16,
        "PoC blob stays tiny ({} bytes)",
        blob.len()
    );

    let mut a = Allocator::new();
    let result = deserialize_2026_body_from_stream(
        &mut a,
        &mut Cursor::new(&blob),
        TEST_MAX_ATOM_LEN,
        false,
    );
    assert!(
        result.is_err(),
        "instruction_count must be rejected before pre-allocation"
    );
}
```
