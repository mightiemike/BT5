### Title
Non-Canonical `count=1` Multi-Atom Group Accepted by Deserializer — (`src/serde_2026/de.rs`)

### Summary

The `deserialize_2026_body_from_stream` function accepts a group encoding with a **negative** `length_val` and `count=1`, which the serializer (`write_atom_table`) never produces. The only guard is `length == 0 || count == 0`; there is no rejection of `count == 1` paired with a negative `length_val`. This means two structurally distinct byte streams deserialize to the same CLVM tree, breaking the one-to-one canonical encoding invariant the format is designed to provide.

---

### Finding Description

**Deserializer path** — `deserialize_2026_body_from_stream` (`src/serde_2026/de.rs`):

```
length_val < 0  →  (length, count) = (-length_val, read_varint())
guard: if length == 0 || count == 0 { Err }
```

When `length_val = -5` and `count = 1`, both guard conditions are false (`5 != 0`, `1 != 0`), so the deserializer proceeds normally and reads one 5-byte atom. [1](#0-0) 

**Serializer path** — `write_atom_table` (`src/serde_2026/ser.rs`):

```rust
if atoms_of_length.len() == 1 {
    write_varint(writer, *length as i64)?;   // always positive
} else {
    write_varint(writer, -(*length as i64))?; // negative only for count > 1
    write_varint(writer, atoms_of_length.len() as i64)?;
}
```

The serializer **unconditionally** uses a positive `length_val` for any group containing exactly one atom. It never emits `negative length_val + count=1`. [2](#0-1) 

The same missing guard exists in `serialized_length_serde_2026`, which mirrors the deserializer's atom-table walk: [3](#0-2) 

**`strict` mode does not help.** The `strict` flag only rejects overlong varint encodings; it has no bearing on the semantic validity of `count=1` paired with a negative `length_val`. [4](#0-3) 

The format specification itself (in `docs/serde-2026.md`) defines the negative-length encoding as exclusively for **multiple** atoms:

> If the group contains **multiple** atoms of the same length: a negative varint encoding the negated byte length, then a positive varint encoding the count…

The deserializer does not enforce this constraint.

---

### Impact Explanation

Two distinct byte streams deserialize to the same CLVM tree:

| Encoding | `length_val` | `count` | Bytes | Result |
|---|---|---|---|---|
| Canonical | `+5` | implicit 1 | `hello` | atom `"hello"` |
| Non-canonical | `-5` | `1` | `hello` | atom `"hello"` (identical tree) |

Any system that:
- uses the raw serde_2026 bytes as a cache key, deduplication key, or commitment,
- or compares blobs for equality rather than comparing the deserialized tree,

will treat these as distinct objects even though they represent the same CLVM value. Conversely, any system that hashes the bytes directly (rather than the tree) will produce different hashes for semantically identical programs.

The fuzz roundtrip harness (`fuzz/fuzz_targets/serde_2026.rs`) does not catch this because it only checks `tree == re-serialized(tree)`, not `bytes == re-serialized(deserialize(bytes))`. [5](#0-4) 

---

### Likelihood Explanation

The non-canonical blob is trivially constructable by hand. No special privileges or internal access are required — any caller of the public `deserialize_2026` / `deserialize_2026_body_from_stream` API with attacker-controlled input can supply it. The `strict=false` path (the default in all fuzz targets and the Python wheel) accepts it without error.

---

### Recommendation

Add an explicit rejection of `count == 1` when `length_val < 0` in both `deserialize_2026_body_from_stream` and `serialized_length_serde_2026`:

```rust
// after computing (length, count) for the negative branch:
if count == 1 {
    return Err(EvalErr::SerializationError); // non-canonical: use positive length for singletons
}
```

This makes the deserializer enforce the same invariant the serializer already upholds, restoring the bijection between canonical byte streams and CLVM trees.

---

### Proof of Concept

Construct the body manually (no magic prefix needed for `deserialize_2026_body_from_stream`):

```
group_count  = 1          → varint 0x01
length_val   = -5         → varint encoding of -5 (e.g. 0x7b in the 7-bit scheme: -5 = 0b1111011 → 0x7b)
count        = 1          → varint 0x01
atom bytes   = "hello"    → 0x68 0x65 0x6c 0x6c 0x6f
instruction_count = 1     → varint 0x01
instruction  = 2          → varint 0x02  (push atom[0])
```

Deserializing this blob with `strict=false` returns `Ok(node)` where `allocator.atom(node) == b"hello"`. Serializing that node with `serialize_2026` produces a blob with `length_val = +5`, not `-5 + count=1`. The two blobs are byte-distinct but tree-equivalent, confirming the invariant violation.

### Citations

**File:** src/serde_2026/de.rs (L50-64)
```rust
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
```

**File:** src/serde_2026/de.rs (L186-197)
```rust
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
```

**File:** src/serde_2026/ser.rs (L164-175)
```rust
    for (length, atoms_of_length) in &atom_groups {
        if atoms_of_length.len() == 1 {
            write_varint(writer, *length as i64)?;
            writer.write_all(tree.allocator.atom(atoms_of_length[0]).as_ref())?;
        } else {
            write_varint(writer, -(*length as i64))?;
            write_varint(writer, atoms_of_length.len() as i64)?;
            for &atom_node in atoms_of_length {
                writer.write_all(tree.allocator.atom(atom_node).as_ref())?;
            }
        }
    }
```

**File:** fuzz/fuzz_targets/serde_2026.rs (L37-43)
```rust
fn roundtrip_check(label: &str, a: &mut Allocator, original: NodePtr, blob: &[u8]) {
    let checkpoint = a.checkpoint();
    let decoded = deserialize_2026(a, blob, FUZZ_MAX_ATOM_LEN, false)
        .unwrap_or_else(|e| panic!("{label}: deserialize failed: {e:?}"));
    assert!(node_eq(a, original, decoded), "{label}: tree mismatch");
    a.restore_checkpoint(&checkpoint);
}
```
