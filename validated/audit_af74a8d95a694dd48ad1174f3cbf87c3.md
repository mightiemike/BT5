### Title
SHA-1 Used as Atom Identity Hash in `TreeCache`, Enabling Back-Reference Collision Attacks - (File: `src/serde/tree_cache.rs`)

### Summary

`TreeCache` in `src/serde/tree_cache.rs` uses SHA-1 (160-bit, 20-byte output) to identify and deduplicate atom nodes during CLVM back-reference serialization. When two distinct atoms produce the same SHA-1 hash, `TreeCache` treats them as identical, merges them into a single `NodeEntry`, and may emit a back-reference path pointing to the wrong atom. This causes `node_to_bytes_backrefs` to produce a serialized form that deserializes to a different CLVM tree than the original — a consensus-breaking divergence between serialization and deserialization.

### Finding Description

**Root cause:** In `src/serde/tree_cache.rs`, the `hash_atom` function computes a salted SHA-1 digest over atom bytes:

```rust
fn hash_atom(salt: &[u8], blob: &[u8]) -> Bytes20 {
    let mut ctx = Sha1::default();
    ctx.update(salt);
    ctx.update(blob);
    ctx.finalize().into()
}
```

The result (`Bytes20 = [u8; 20]`) is used as the key in `atom_lookup: HashMap<Bytes20, u32, RandomState>`. When a new atom is hashed and its SHA-1 collides with an existing entry, the code takes the `Entry::Occupied` branch and reuses the existing `NodeEntry` index — treating the two distinct atoms as identical:

```rust
let ne = match self.atom_lookup.entry(hash) {
    Entry::Occupied(ne) => {
        let idx = *ne.get();
        e.insert(idx);
        stack.push(idx);
        continue;   // ← skips creating a new NodeEntry; atoms are merged
    }
    Entry::Vacant(ne) => ne,
};
```

This means `find_path()` will return a path to the colliding atom when asked for a path to the new (distinct) atom. The `Serializer` in `src/serde/incremental.rs` and `node_to_bytes_backrefs` in `src/serde/ser_br.rs` then emit a `0xfe` back-reference byte followed by that path, causing the deserializer to reconstruct the wrong atom at that position.

The code acknowledges SHA-1's weakness and attempts to mitigate it with an 8-byte random salt per `TreeCache` instance:

```rust
/// We compute hash-trees using SHA-1 in order to determine whether the
/// trees are identical or not. To mitigate malicious SHA-1 hash collisions,
/// we salt the hashes
salt: [u8; 8],
```

However, the salt is generated at `TreeCache::new()` time using `rand::rng()` and is **not known to the attacker before serialization begins**. This means a chosen-prefix or birthday attack must be performed against the salted SHA-1 function, which is harder but not impossible given SHA-1's known collision vulnerabilities (SHAttered, etc.).

**Concrete broken invariant:** For any two distinct atoms `A` and `B` where `SHA1(salt || A) == SHA1(salt || B)`, `node_to_bytes_backrefs` will serialize a tree containing both `A` and `B` such that the deserialized tree replaces one with the other. The serialized bytes no longer faithfully represent the original CLVM tree.

**Entry path:** An attacker submits a CLVM program (attacker-controlled bytes) to any caller of `node_to_bytes_backrefs` or the incremental `Serializer`. This is a standard, externally reachable code path used for compressed CLVM serialization. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Impact Explanation

If a SHA-1 collision is achieved under the random salt, the serializer emits a back-reference to the wrong atom. The deserializer faithfully follows the path and reconstructs a different atom. The resulting CLVM tree is semantically different from the original. In a blockchain context:

- A puzzle or solution serialized with back-references could deserialize to a different program, causing nodes to disagree on the puzzle hash or solution validity — a **consensus divergence**.
- A coin's puzzle hash is derived from the tree hash of the puzzle. If the serialized form of a puzzle is corrupted by a back-reference collision, nodes that re-hash the deserialized form will compute a different puzzle hash than the one committed on-chain.
- Any system that round-trips CLVM through `node_to_bytes_backrefs` → `node_from_bytes_backrefs` and then re-evaluates or re-hashes the result is vulnerable.

### Likelihood Explanation

SHA-1 is a 160-bit hash. The random 8-byte (64-bit) salt does not increase the collision resistance of SHA-1 itself — it only prevents pre-computed collision pairs from being reused across different `TreeCache` instances. An attacker who can observe the salt (e.g., by triggering serialization of a known atom and observing the output) or who can perform a chosen-prefix attack against salted SHA-1 can still find a collision. The SHAttered attack demonstrated practical SHA-1 chosen-prefix collisions in 2017. The effective security is therefore significantly below 80 bits. For a high-value target (e.g., a large coin spend), a well-resourced attacker could feasibly find a collision. This is the same class of risk as the PartyDAO report's 15-byte hash finding, but with a larger (20-byte) hash and a salt that partially mitigates offline precomputation.

### Recommendation

Replace SHA-1 with SHA-256 (or at minimum SHA-256 truncated to 20 bytes) for the `atom_lookup` key. The `Bytes20` type and `hash_atom` function should be updated to use a 32-byte SHA-256 digest. The salt can be retained as an additional defense-in-depth measure. Alternatively, use the full 32-byte SHA-256 tree hash (already computed elsewhere in the codebase via `treehash` in `src/serde/object_cache.rs`) as the atom identity key, eliminating the need for a separate weaker hash entirely. [5](#0-4) 

### Proof of Concept

1. Find two byte strings `A` and `B` such that `SHA1(salt || A) == SHA1(salt || B)` for a known salt value. (With SHA-1's known weaknesses, this is feasible for a determined attacker.)
2. Construct a CLVM tree containing both atoms `A` and `B` as distinct leaves, e.g., `(A . B)`.
3. Call `node_to_bytes_backrefs(allocator, root)` on this tree.
4. The `TreeCache` will merge `A` and `B` into the same `NodeEntry`. When serializing `B`, `find_path` returns a path to `A` (already serialized), and a `0xfe` back-reference is emitted.
5. Call `node_from_bytes_backrefs` on the resulting bytes.
6. The deserialized tree is `(A . A)` instead of `(A . B)` — the two atoms are no longer distinct.
7. Computing the tree hash of the deserialized tree yields a different value than the tree hash of the original, demonstrating consensus divergence.

The salt randomization means the attacker must either: (a) observe the salt by triggering a serialization of a known atom and reverse-engineering the salt from the output, or (b) use a chosen-prefix SHA-1 attack that works for any salt value. Both are within the capability of a well-resourced attacker targeting a high-value Chia coin. [6](#0-5) [7](#0-6)

### Citations

**File:** src/serde/tree_cache.rs (L14-21)
```rust
type Bytes20 = [u8; 20];

fn hash_atom(salt: &[u8], blob: &[u8]) -> Bytes20 {
    let mut ctx = Sha1::default();
    ctx.update(salt);
    ctx.update(blob);
    ctx.finalize().into()
}
```

**File:** src/serde/tree_cache.rs (L106-114)
```rust
    /// maps tree-hashes to the index of the corresponding NodeEntry in the
    /// node_entries vector. For any given tree hash, we're only supposed to
    /// have a single NodeEntry. There may be multiple NodePtr referring to
    /// the same NodeEntry (if they are identical sub trees).
    atom_lookup: HashMap<Bytes20, u32, RandomState>,

    /// maps left + right child indices to the index of the pair with those
    /// children. This is the atom_lookup counterpart for pairs
    pair_lookup: HashMap<u64, u32>,
```

**File:** src/serde/tree_cache.rs (L134-138)
```rust
    /// We compute hash-trees using SHA-1 in order to determine whether the
    /// trees are identical or not. To mitigate malicious SHA-1 hash collisions,
    /// we salt the hashes
    salt: [u8; 8],
}
```

**File:** src/serde/tree_cache.rs (L140-148)
```rust
impl TreeCache {
    pub fn new(sentinel: Option<NodePtr>) -> Self {
        let mut rng = rand::rng();
        Self {
            sentinel_node: sentinel,
            atom_lookup: HashMap::with_hasher(RandomState::default()),
            salt: rng.random(),
            ..Default::default()
        }
```

**File:** src/serde/tree_cache.rs (L244-261)
```rust
                    let hash = hash_atom(&self.salt, buf.as_ref());

                    // record the mapping of this node to the
                    // corresponding NodeEntry index
                    // now that we've hashed the node, it might be
                    // identical to an existing one. If so, use the
                    // same NodeEntry, otherwise, add a new one.
                    let ne = match self.atom_lookup.entry(hash) {
                        Entry::Occupied(ne) => {
                            // we already have a node with this
                            // hash
                            let idx = *ne.get();
                            e.insert(idx);
                            stack.push(idx);
                            continue;
                        }
                        Entry::Vacant(ne) => ne,
                    };
```

**File:** src/serde/object_cache.rs (L99-114)
```rust
/// calculate the standard `sha256tree` has for a node
pub fn treehash(
    cache: &mut ObjectCache<Bytes32>,
    allocator: &Allocator,
    node: NodePtr,
) -> Option<Bytes32> {
    match allocator.sexp(node) {
        SExp::Pair(left, right) => match cache.get_from_cache(&left) {
            None => None,
            Some(left_value) => cache
                .get_from_cache(&right)
                .map(|right_value| hash_blobs(&[&[2], left_value, right_value])),
        },
        SExp::Atom => Some(hash_blobs(&[&[1], allocator.atom(node).as_ref()])),
    }
}
```

**File:** src/serde/incremental.rs (L75-95)
```rust
            match self.tree_cache.find_path(node_to_write) {
                Some(path) => {
                    self.output.write_all(&[BACK_REFERENCE])?;
                    write_atom(&mut self.output, &path)?;
                    self.tree_cache.push(node_to_write);
                }
                None => match a.sexp(node_to_write) {
                    SExp::Pair(left, right) => {
                        self.output.write_all(&[CONS_BOX_MARKER])?;
                        self.write_stack.push(right);
                        self.write_stack.push(left);
                        self.read_op_stack.push(ReadOp::Cons(node_to_write));
                        self.read_op_stack.push(ReadOp::Parse);
                        self.read_op_stack.push(ReadOp::Parse);
                    }
                    SExp::Atom => {
                        let atom = a.atom(node_to_write);
                        write_atom(&mut self.output, atom.as_ref())?;
                        self.tree_cache.push(node_to_write);
                    }
                },
```
