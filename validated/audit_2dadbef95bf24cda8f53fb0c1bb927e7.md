### Title
Non-canonical serde_2026 Deserialization Accepts Duplicate Atoms in Atom Table — (File: `src/serde_2026/de.rs`)

---

### Summary

The `deserialize_2026_body_from_stream` function does not enforce uniqueness of atom byte content in the atom table. An attacker can craft a serde_2026 blob where the same byte sequence appears at two or more distinct atom indices. The deserializer accepts this silently, producing a tree that cannot be re-serialized to the same blob. This breaks the canonical-serialization invariant the format is designed to guarantee and creates a non-canonical deserialization path reachable from attacker-controlled bytes.

---

### Finding Description

The serde_2026 format specification explicitly states the atom table contains **"all unique atoms (except nil)"**. The serializer enforces this: `intern_tree` in `src/serde/intern.rs` deduplicates atoms by byte content via `atom_to_interned: HashMap<Atom, NodePtr>` before `write_atom_table` emits them.

The deserializer in `src/serde_2026/de.rs` performs no such check. At lines 66–71, every atom read from the wire is unconditionally appended to the `atoms` vector:

```rust
for _ in 0..count {
    reader
        .read_exact(&mut buf)
        .map_err(|_| EvalErr::SerializationError)?;
    atoms.push(allocator.new_atom(&buf)?);   // ← no uniqueness check
}
```

An attacker can craft a blob with two separate atom table entries that carry identical byte content, e.g., two singleton groups each containing `\x01`. The instruction stream can then reference both indices (e.g., `N=2` for index 0 and `N=3` for index 1), producing a tree where two structurally distinct `NodePtr` values hold the same byte content. The deserializer returns `Ok` and the resulting `NodePtr` is a valid CLVM tree.

When that tree is subsequently passed to `serialize_2026` (which calls `intern_tree` and deduplicates), the output blob is shorter and structurally different from the input blob. The round-trip `deserialize → serialize` is not idempotent for attacker-crafted inputs, violating the canonical-serialization contract.

The same gap exists in `serialized_length_serde_2026` (`src/serde_2026/de.rs` lines 183–213): it mirrors the deserializer's header-time validation but also performs no atom-uniqueness check, so a caller using it as a pre-gate before deserialization cannot detect this malformation.

---

### Impact Explanation

**Broken invariant:** The format guarantees each atom byte sequence appears exactly once in the atom table. The deserializer does not enforce this, so the same logical CLVM tree has multiple valid serde_2026 encodings.

**Concrete consequences:**

1. **Consensus divergence risk.** If serde_2026 is used in a consensus-critical path (block generators, coin puzzles) and different implementations or future versions enforce atom uniqueness at decode time while the current one does not, nodes will disagree on blob validity, risking a chain split.

2. **Blob-identity confusion.** Any system that uses the raw serde_2026 blob as a cache key or deduplication key (rather than the SHA-256 tree hash) will treat two blobs encoding the same tree as distinct objects. An attacker can inflate caches or bypass deduplication by submitting the same program in multiple non-canonical encodings.

3. **Round-trip non-idempotency.** `deserialize_2026(blob) → serialize_2026(tree)` produces a different blob for attacker-crafted inputs. Any pipeline that re-serializes after deserialization and then compares to the original will observe a mismatch.

The SHA-256 tree hash is unaffected (it is content-based, not index-based), so tree-hash-based validation is not broken. The impact is scoped to blob-level canonicality and any system that relies on it.

---

### Likelihood Explanation

Crafting a malformed serde_2026 blob with duplicate atoms requires only knowledge of the wire format (documented in `docs/serde-2026.md`) and the ability to supply bytes to `deserialize_2026` or `deserialize_2026_body_from_stream`. Both are exposed via the Python wheel API (`wheel/src/api.rs`). No special privileges are required. The attacker-controlled entry path is direct: any caller that deserializes an externally supplied serde_2026 blob is reachable.

---

### Recommendation

Add a uniqueness check in `deserialize_2026_body_from_stream` when populating the `atoms` vector. The simplest approach is to maintain a `HashSet<Vec<u8>>` (or a sorted structure) of seen atom byte sequences and return `Err(EvalErr::SerializationError)` if a duplicate is encountered:

```rust
let mut seen_atoms: std::collections::HashSet<Vec<u8>> = HashSet::new();
// inside the inner loop:
if !seen_atoms.insert(buf.clone()) {
    return Err(EvalErr::SerializationError);
}
atoms.push(allocator.new_atom(&buf)?);
```

Mirror the same check in `serialized_length_serde_2026` so the length probe and the deserializer remain in sync (as the existing test `test_serialized_length_rejects_what_deserialize_rejects` verifies for other conditions).

---

### Proof of Concept

Construct a minimal serde_2026 body (no magic prefix, for `deserialize_2026_body_from_stream`) with two singleton atom groups both containing `\x01`, then an instruction stream that references both indices and conses them:

```
group_count = 2          → varint 0x02
group 0: length=1, atom=\x01  → varint 0x01, byte 0x01
group 1: length=1, atom=\x01  → varint 0x01, byte 0x01   ← duplicate
instruction_count = 3    → varint 0x03
inst 0: push atom[0]     → varint 0x02  (N=2 → index 0)
inst 1: push atom[1]     → varint 0x03  (N=3 → index 1)
inst 2: cons             → varint 0x01
```

Encoded: `[0x02, 0x01, 0x01, 0x01, 0x01, 0x03, 0x02, 0x03, 0x01]`

`deserialize_2026_body_from_stream` returns `Ok(pair_node)` where both children are atoms with content `\x01` but distinct `NodePtr` values. Re-serializing via `serialize_2026` produces a blob with only one atom table entry for `\x01` and a back-reference instruction — a structurally different, shorter blob — confirming the round-trip is not idempotent. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/serde_2026/de.rs (L45-72)
```rust
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
    }
```

**File:** src/serde_2026/de.rs (L175-226)
```rust
pub fn serialized_length_serde_2026(buf: &[u8], max_atom_len: usize, strict: bool) -> Result<u64> {
    if !buf.starts_with(&SERDE_2026_MAGIC_PREFIX) {
        return Err(EvalErr::SerializationError);
    }

    let data = &buf[SERDE_2026_MAGIC_PREFIX.len()..];
    let mut cursor = Cursor::new(data);

    let group_count = checked_usize(read_varint(&mut cursor, strict)?)?;
    for _ in 0..group_count {
        let length_val = read_varint(&mut cursor, strict)?;
        let skip = if length_val < 0 {
            if length_val == i64::MIN {
                return Err(EvalErr::SerializationError);
            }
            let atom_len = checked_bounded_usize(-length_val, max_atom_len)?;
            let count = checked_usize(read_varint(&mut cursor, strict)?)?;
            if atom_len == 0 || count == 0 {
                return Err(EvalErr::SerializationError);
            }
            (atom_len as u64)
                .checked_mul(count as u64)
                .ok_or(EvalErr::SerializationError)?
        } else {
            let atom_len = checked_bounded_usize(length_val, max_atom_len)?;
            if atom_len == 0 {
                return Err(EvalErr::SerializationError);
            }
            atom_len as u64
        };
        let new_pos = cursor
            .position()
            .checked_add(skip)
            .ok_or(EvalErr::SerializationError)?;
        if new_pos > data.len() as u64 {
            return Err(EvalErr::SerializationError);
        }
        cursor.set_position(new_pos);
    }

    let instruction_count = checked_usize(read_varint(&mut cursor, strict)?)?;
    // Mirror `deserialize_2026_body_from_stream`: instruction_count == 0
    // leaves the stack empty and is rejected there, so reject it here too.
    if instruction_count == 0 {
        return Err(EvalErr::SerializationError);
    }
    for _ in 0..instruction_count {
        read_varint(&mut cursor, strict)?;
    }

    Ok(SERDE_2026_MAGIC_PREFIX.len() as u64 + cursor.position())
}
```

**File:** src/serde/intern.rs (L70-95)
```rust
    // Maps atom content to interned NodePtr (for deduplication)
    let mut atom_to_interned: HashMap<Atom, NodePtr> = HashMap::new();
    // Maps (left_interned, right_interned) to interned pair NodePtr
    let mut pair_to_interned: HashMap<(NodePtr, NodePtr), NodePtr> = HashMap::new();

    let mut stack = vec![node];

    while let Some(current) = stack.pop() {
        // Skip if already processed
        if node_to_interned.contains_key(&current) {
            continue;
        }

        match source.sexp(current) {
            SExp::Atom => {
                let atom = source.atom(current);
                let interned = match atom_to_interned.entry(atom) {
                    Entry::Occupied(o) => *o.get(),
                    Entry::Vacant(v) => {
                        let new_node = new_allocator.new_atom(atom.as_ref())?;
                        v.insert(new_node);
                        atoms.push(new_node);
                        new_node
                    }
                };
                node_to_interned.insert(current, interned);
```

**File:** docs/serde-2026.md (L32-65)
```markdown
1. **Atom table** — all unique atoms (except nil), grouped by length
2. **Instruction stream** — stack-based operations to reconstruct the tree

### Atom Table

Nil (the empty atom) is **not** included in the atom table — it has a dedicated
opcode (`0`) in the instruction stream.

The atom table begins with a varint encoding the number of atom groups.

For each group (in stream order):

- If the group contains **one** atom: a positive varint encoding the atom's byte
  length, followed by the atom's raw bytes.
- If the group contains **multiple** atoms of the same length: a negative varint
  encoding the negated byte length, then a positive varint encoding the count,
  then the raw bytes of each atom concatenated (each is exactly `length` bytes).

Atom lengths must be non-zero because nil is excluded from the atom table.
Deserializers enforce a configurable maximum atom length (default: 1 MiB) and a
maximum input byte budget (default: 10 MiB). Separate atom-group, atom-count,
instruction-count, stack-size, and pair-count limits are not needed for DoS
protection: every declared item must consume at least one input byte before it
can produce parser work or allocate a CLVM node. The input byte budget therefore
bounds all of those quantities.

Atoms are assigned indices starting from 0, in the order they appear in the
table.

The decoder accepts groups in any order. Multiple groups with the same byte
length are valid (they contribute separate atom indices). A serializer may
choose a specific ordering strategy (for example, sorting by frequency so
commonly-referenced atoms land in lower index ranges whose varint encodings are
shorter).
```
