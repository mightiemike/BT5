### Title
`TreeCache::default()` Leaves Security-Critical `salt` Uninitialized (Zero) — (`File: src/serde/tree_cache.rs`)

---

### Summary

`TreeCache` derives `Default` but the derived implementation leaves the `salt` field as `[0u8; 8]` (all zeros). The correct constructor `TreeCache::new()` initializes `salt` with cryptographically random bytes specifically to mitigate SHA-1 collision attacks. Any caller using `TreeCache::default()` — including the publicly exported API — silently receives a security-weakened instance where the collision protection is entirely absent.

---

### Finding Description

`TreeCache` is declared with `#[derive(Default)]` at line 94: [1](#0-0) 

The struct contains a `salt: [u8; 8]` field, documented explicitly as a security measure:

```
/// We compute hash-trees using SHA-1 in order to determine whether the
/// trees are identical or not. To mitigate malicious SHA-1 hash collisions,
/// we salt the hashes
salt: [u8; 8],
``` [2](#0-1) 

The correct constructor `TreeCache::new()` initializes `salt` with random bytes:

```rust
pub fn new(sentinel: Option<NodePtr>) -> Self {
    let mut rng = rand::rng();
    Self {
        sentinel_node: sentinel,
        atom_lookup: HashMap::with_hasher(RandomState::default()),
        salt: rng.random(),   // <-- random, secure
        ..Default::default()
    }
}
``` [3](#0-2) 

The derived `Default` implementation, however, initializes `salt` to `[0u8; 8]` (the `Default` for `[u8; 8]`). This is the same pattern as the external report: a type inherits/derives an initializer that does not properly set security-critical state, while the correct constructor does.

The `salt` is consumed in `hash_atom()`, which is called for every atom during `update()`:

```rust
fn hash_atom(salt: &[u8], blob: &[u8]) -> Bytes20 {
    let mut ctx = Sha1::default();
    ctx.update(salt);
    ctx.update(blob);
    ctx.finalize().into()
}
``` [4](#0-3) 

With a zero salt, the SHA-1 hash of every atom is fully deterministic and attacker-predictable, making SHA-1 chosen-prefix collision attacks (e.g., SHAttered) directly applicable with no per-instance randomness to defeat.

`TreeCache` and its `Default` implementation are publicly exported from the crate: [5](#0-4) 

---

### Impact Explanation

The `salt` exists to prevent an attacker from crafting two distinct atom byte sequences that produce the same SHA-1 hash, which would cause `atom_lookup` to treat them as the same node. If two distinct atoms are aliased in `atom_lookup`, `find_path()` returns a back-reference path that points to the wrong atom. The serialized output then contains an incorrect back-reference, causing the deserialized CLVM tree to differ from the original — a consensus-divergence class bug for any node that serializes programs using a zero-salt `TreeCache`.

With a zero salt, the SHA-1 input is entirely attacker-controlled (just the atom bytes), making a collision attack straightforward to precompute offline. The attacker submits two atoms whose SHA-1 hashes collide; the serializer emits a back-reference to the wrong one; the deserializing node reconstructs a different program tree than was serialized. [6](#0-5) 

---

### Likelihood Explanation

`TreeCache` is a public type. Any downstream consumer of the `clvmr` crate that calls `TreeCache::default()` — a natural Rust idiom — receives the broken instance. The fuzz harness itself already uses `TreeCache::default()` at line 26 of `fuzz/fuzz_targets/tree_cache.rs`, demonstrating that this is a realistic and expected usage pattern. The `#[derive(Default)]` annotation actively invites this usage. SHA-1 chosen-prefix collisions are practically feasible (SHAttered, 2017), so the threat is not merely theoretical. [7](#0-6) 

---

### Recommendation

Remove the `#[derive(Default)]` attribute from `TreeCache`. Replace it with a manual `Default` implementation that delegates to `TreeCache::new(None)`, ensuring the `salt` is always randomly initialized:

```rust
impl Default for TreeCache {
    fn default() -> Self {
        Self::new(None)
    }
}
```

This mirrors the pattern used by `Allocator` and `ReadCacheLookup`, both of which implement `Default` by delegating to their `new()` constructors: [8](#0-7) [9](#0-8) 

---

### Proof of Concept

```rust
use clvmr::Allocator;
use clvmr::serde::TreeCache;

let mut a = Allocator::new();
// Craft two atoms whose SHA-1(zeros_salt || bytes) collide (precomputed offline)
let atom1 = a.new_atom(&COLLISION_BYTES_A).unwrap();
let atom2 = a.new_atom(&COLLISION_BYTES_B).unwrap();
let pair  = a.new_pair(atom1, atom2).unwrap();

// Using the broken default — salt is [0,0,0,0,0,0,0,0]
let mut cache = TreeCache::default();
cache.update(&a, pair);

// atom1 and atom2 alias to the same NodeEntry in atom_lookup.
// find_path(atom2) returns the path to atom1, producing a corrupted back-reference.
cache.push(atom1);
let path = cache.find_path(atom2); // returns Some(path_to_atom1) — wrong node
assert!(path.is_some()); // back-reference points to the wrong atom
``` [3](#0-2) [10](#0-9)

### Citations

**File:** src/serde/tree_cache.rs (L16-21)
```rust
fn hash_atom(salt: &[u8], blob: &[u8]) -> Bytes20 {
    let mut ctx = Sha1::default();
    ctx.update(salt);
    ctx.update(blob);
    ctx.finalize().into()
}
```

**File:** src/serde/tree_cache.rs (L94-138)
```rust
#[derive(Default)]
pub struct TreeCache {
    /// caches extra metadata about a tree of nodes. The value is an index into
    /// the node_entries vector.
    node_map: HashMap<NodePtr, u32>,

    /// The metadata for all nodes in the tree. This is like a shadow tree
    /// structure to the NodePtr one. The most important difference is that
    /// identical nodes are merged, using the same NodeEntry, and additional
    /// metadata is kept, such as the tree hash.
    node_entries: Vec<NodeEntry>,

    /// maps tree-hashes to the index of the corresponding NodeEntry in the
    /// node_entries vector. For any given tree hash, we're only supposed to
    /// have a single NodeEntry. There may be multiple NodePtr referring to
    /// the same NodeEntry (if they are identical sub trees).
    atom_lookup: HashMap<Bytes20, u32, RandomState>,

    /// maps left + right child indices to the index of the pair with those
    /// children. This is the atom_lookup counterpart for pairs
    pair_lookup: HashMap<u64, u32>,

    /// When deserializing, we keep a stack of nodes we've parsed so far, this
    /// stack is maintaining that same state, since that's what back-references
    /// are pointing into.
    stack: Vec<u32>,

    /// This records which NodeEntries have been serialized so far. When we look
    /// for back-references, we can only pick nodes in this set. nodes with
    /// small serialized length are not inserted. This set is built and
    /// updated as we serialize, to ensure we only include nodes that *can* be
    /// referenced.
    serialized_nodes: BitSet,

    /// if the sentinel node is set, we can't compute the tree hashes or
    /// serialized length for this node nor any of its ancestors. When calling
    /// update(), the tree is assumed to be placed at the sentinel node in the
    /// previous call to update()
    pub sentinel_node: Option<NodePtr>,

    /// We compute hash-trees using SHA-1 in order to determine whether the
    /// trees are identical or not. To mitigate malicious SHA-1 hash collisions,
    /// we salt the hashes
    salt: [u8; 8],
}
```

**File:** src/serde/tree_cache.rs (L141-149)
```rust
    pub fn new(sentinel: Option<NodePtr>) -> Self {
        let mut rng = rand::rng();
        Self {
            sentinel_node: sentinel,
            atom_lookup: HashMap::with_hasher(RandomState::default()),
            salt: rng.random(),
            ..Default::default()
        }
    }
```

**File:** src/serde/tree_cache.rs (L244-272)
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
                    let idx = self.node_entries.len() as u32;
                    ne.insert(idx);
                    e.insert(idx);
                    stack.push(idx);
                    let serialized_length = serialized_length_atom(buf.as_ref());
                    self.node_entries.push(NodeEntry {
                        tree_hash: Some(hash),
                        parents: vec![],
                        serialized_length: u64::from(serialized_length),
                        on_stack: 0,
                    });
```

**File:** src/serde/mod.rs (L43-43)
```rust
pub use tree_cache::{TreeCache, TreeCacheCheckpoint};
```

**File:** src/allocator.rs (L311-314)
```rust
impl Default for Allocator {
    fn default() -> Self {
        Self::new()
    }
```

**File:** src/serde/read_cache_lookup.rs (L41-44)
```rust
impl Default for ReadCacheLookup {
    fn default() -> Self {
        Self::new()
    }
```
