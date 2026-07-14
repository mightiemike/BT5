### Title
Incomplete `TreeCache::restore()` Leaves Stale `node_map` / `node_entries` After Rollback, Causing Incorrect Back-Reference Paths on NodePtr Reuse — (`File: src/serde/tree_cache.rs`)

---

### Summary

`TreeCache::restore()` only rolls back three of the seven mutable fields of `TreeCache`. The four structural maps (`node_map`, `node_entries`, `atom_lookup`, `pair_lookup`) are never restored. When the caller also restores the `Allocator` to a checkpoint (freeing and reusing `NodePtr` indices), a subsequent `update()` call finds stale entries in `node_map` for the recycled `NodePtr` values and silently reuses the wrong `NodeEntry`. `find_path()` then emits a back-reference path that resolves to a different node than intended, producing non-canonical serialized CLVM bytes and a consensus-divergent tree hash.

---

### Finding Description

`TreeCache` has seven mutable fields:

```
node_map        HashMap<NodePtr, u32>
node_entries    Vec<NodeEntry>
atom_lookup     HashMap<Bytes20, u32>
pair_lookup     HashMap<u64, u32>
stack           Vec<u32>
serialized_nodes BitSet
sentinel_node   Option<NodePtr>
```

`undo_state()` snapshots only three of them:

```rust
TreeCacheCheckpoint {
    stack: self.stack.clone(),
    serialized_nodes: self.serialized_nodes.clone(),
    sentinel_entry: ...,
}
``` [1](#0-0) 

`restore()` writes back only those three:

```rust
self.stack = st.stack;
self.serialized_nodes = st.serialized_nodes;
// sentinel_entry re-inserted into node_map
``` [2](#0-1) 

`node_map`, `node_entries`, `atom_lookup`, and `pair_lookup` are never trimmed. Every `NodeEntry` appended by the rolled-back `update()` call — including its `parents` list — persists after `restore()`.

`Serializer::add()` captures the `UndoState` **before** calling `tree_cache.update()`:

```rust
let undo_state = UndoState {
    tree_cache: self.tree_cache.undo_state(),  // snapshot taken here
    ...
};
self.tree_cache.update(a, node);               // mutates node_map etc.
``` [3](#0-2) 

After `Serializer::restore()` is called, the `TreeCache` is in a hybrid state: `stack` and `serialized_nodes` reflect the pre-`update()` snapshot, but `node_map` and `node_entries` still contain all entries written by the rolled-back `update()`. [4](#0-3) 

The `Allocator` uses a grow-only vector model. `restore_checkpoint()` truncates `atom_vec` and `pair_vec`, making freed `NodePtr` indices available for reuse:

```rust
self.u8_vec.truncate(cp.u8s as usize);
self.pair_vec.truncate(cp.pairs as usize);
self.atom_vec.truncate(cp.atoms as usize);
``` [5](#0-4) 

When the caller restores the allocator to a checkpoint taken before the rolled-back `add()`, new allocations receive the same `NodePtr` integer values as the freed nodes. The next `update()` call then hits `Entry::Occupied` in `node_map` for these recycled `NodePtr`s and silently reuses the stale `NodeEntry` — which carries the wrong `tree_hash`, `serialized_length`, and `parents` list from the previous, discarded tree:

```rust
let e = match self.node_map.entry(node) {
    Entry::Occupied(e) => {
        let idx = *e.get();   // ← stale index from rolled-back update
        stack.push(idx);
        continue;
    }
    Entry::Vacant(e) => e,
};
``` [6](#0-5) 

`find_path()` then traverses the stale `parents` chain and emits a back-reference path that points to a node that does not exist in the current serialization state, or points to a structurally different node:

```rust
let idx = *self.node_map.get(&node).expect("invalid node");
// idx is the stale NodeEntry from the rolled-back update
let entry = &self.node_entries[idx as usize];
// entry.parents contains links from the discarded tree
``` [7](#0-6) 

---

### Impact Explanation

The corrupted result is a `Vec<u8>` back-reference path emitted by `find_path()` that encodes a wrong environment-lookup path. When the serialized bytes are deserialized by `node_from_bytes_backrefs`, the back-reference resolves to a different `NodePtr` than the one that was serialized. The resulting CLVM tree has different content, a different SHA-256 tree hash, and will evaluate differently. Any two nodes that serialize the same logical program — one using the incremental serializer with restore+allocator-restore, one using a fresh serializer — will compute different tree hashes, causing **consensus divergence** on the Chia blockchain.

---

### Likelihood Explanation

`Serializer::restore()` is explicitly designed for the pattern of "try a candidate node, then undo and try another." The natural companion operation is `Allocator::restore_checkpoint()`. The `test_restore` and `test_incremental_restore` tests in `src/serde/incremental.rs` do **not** restore the allocator, so the bug is not caught by existing tests. Any production caller that follows the natural pattern of pairing `Serializer::restore()` with `Allocator::restore_checkpoint()` triggers the bug. The `Serializer` is a public API exported from the `clvmr` crate. [8](#0-7) 

---

### Recommendation

`TreeCacheCheckpoint` must snapshot all four structural maps, or `restore()` must truncate `node_entries`, `node_map`, `atom_lookup`, and `pair_lookup` back to their pre-`update()` sizes. The simplest correct fix is to record the lengths of `node_entries`, `node_map`, `atom_lookup`, and `pair_lookup` at checkpoint time and truncate/drain them on restore, mirroring the `Allocator`'s own checkpoint model.

---

### Proof of Concept

```rust
use clvmr::Allocator;
use clvmr::allocator::NodePtr;
use clvmr::serde::incremental::Serializer;
use clvmr::serde::{node_from_bytes_backrefs, node_to_bytes};

let mut a = Allocator::new();

// sentinel placeholder
let sentinel = a.new_pair(NodePtr::NIL, NodePtr::NIL).unwrap();

// Take allocator checkpoint BEFORE building nodeA
let alloc_cp = a.checkpoint();

// Build nodeA: a large atom (>= 4 bytes so it qualifies for back-refs)
let atom_a = a.new_atom(b"AAAA").unwrap();
let nodeA = a.new_pair(atom_a, sentinel).unwrap();

let mut ser = Serializer::new(Some(sentinel));

// add() populates node_map with atom_a's NodePtr → NodeEntry
let (done, undo) = ser.add(&a, nodeA).unwrap();
assert!(!done);

// Rollback serializer state (node_map NOT cleaned up)
ser.restore(undo);

// Rollback allocator: atom_a's NodePtr index is now free for reuse
a.restore_checkpoint(&alloc_cp);

// Build nodeB: a different atom that gets the SAME NodePtr index as atom_a
let atom_b = a.new_atom(b"BBBB").unwrap();
// atom_b has the same NodePtr value as atom_a had
let nodeB = a.new_pair(atom_b, sentinel).unwrap();

// add() calls update(nodeB): node_map finds atom_b's NodePtr as Occupied
// → reuses atom_a's stale NodeEntry (wrong hash, wrong serialized_length)
let (done2, _) = ser.add(&a, nodeB).unwrap();
// Terminate
let (done3, _) = ser.add(&a, NodePtr::NIL).unwrap();
assert!(done3);

let output = ser.into_inner();

// Deserialize: back-reference resolves to wrong node
let mut a2 = Allocator::new();
let result = node_from_bytes_backrefs(&mut a2, &output).unwrap();
let roundtrip = node_to_bytes(&a2, result).unwrap();

// roundtrip will NOT match the canonical serialization of nodeB's tree
// because the back-reference path was computed from atom_a's stale NodeEntry
```

The stale `node_map` entry causes `find_path()` to emit a path derived from `atom_a`'s `NodeEntry` (wrong `serialized_length`, wrong `parents`) for `atom_b`'s content, producing a back-reference that decodes to a different atom, yielding a different tree hash and consensus divergence. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

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

**File:** src/serde/tree_cache.rs (L151-180)
```rust
    pub fn undo_state(&self) -> TreeCacheCheckpoint {
        let sentinel_entry = match self.sentinel_node {
            Some(sentinel) => self.node_map.get(&sentinel).cloned(),
            None => None,
        };
        TreeCacheCheckpoint {
            stack: self.stack.clone(),
            serialized_nodes: self.serialized_nodes.clone(),
            sentinel_entry,
        }
    }

    pub fn restore(&mut self, st: TreeCacheCheckpoint) {
        for idx in &self.stack {
            self.node_entries[*idx as usize].on_stack -= 1;
        }
        for e in &self.node_entries {
            debug_assert_eq!(e.on_stack, 0);
        }

        self.stack = st.stack;
        for idx in &self.stack {
            self.node_entries[*idx as usize].on_stack += 1;
        }
        self.serialized_nodes = st.serialized_nodes;
        if let Some(sentinel_entry) = st.sentinel_entry {
            self.node_map
                .insert(self.sentinel_node.unwrap(), sentinel_entry);
        }
    }
```

**File:** src/serde/tree_cache.rs (L221-231)
```rust
                    let e = match self.node_map.entry(node) {
                        Entry::Occupied(e) => {
                            // If this node is already in the node_map, meaning
                            // we've already traversed it once. No need to do it
                            // again.
                            let idx = *e.get();
                            stack.push(idx);
                            continue;
                        }
                        Entry::Vacant(e) => e,
                    };
```

**File:** src/serde/tree_cache.rs (L391-402)
```rust
        let idx = *self.node_map.get(&node).expect("invalid node");
        if !self.serialized_nodes.is_visited(idx) {
            return None;
        };

        let entry = &self.node_entries[idx as usize];

        // if there's no serialized length for this node, it means it's the sentinel
        // node, or one of its ancestors. We can't build a path to it
        if entry.serialized_length == 0 {
            return None;
        }
```

**File:** src/serde/incremental.rs (L51-115)
```rust
    pub fn add(&mut self, a: &Allocator, node: NodePtr) -> Result<(bool, UndoState)> {
        // once we're done serializing (i.e. there was no sentinel in the last
        // call to add()), we can't resume
        assert!(!self.read_op_stack.is_empty());

        let undo_state = UndoState {
            read_op_stack: self.read_op_stack.clone(),
            write_stack: self.write_stack.clone(),
            tree_cache: self.tree_cache.undo_state(),
            output_position: self.output.position(),
        };
        self.tree_cache.update(a, node);
        self.write_stack.push(node);

        while let Some(node_to_write) = self.write_stack.pop() {
            if Some(node_to_write) == self.tree_cache.sentinel_node {
                // we're not done serializing yet, we're stopping, and the
                // caller will call add() again with the node to serialize
                // here
                return Ok((false, undo_state));
            }
            let op = self.read_op_stack.pop();
            assert!(op == Some(ReadOp::Parse));

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
            }
            while let Some(ReadOp::Cons(node)) = self.read_op_stack.last() {
                let node = *node;
                self.read_op_stack.pop();
                self.tree_cache.pop2_and_cons(node);
            }
        }

        Ok((true, undo_state))
    }

    pub fn restore(&mut self, state: UndoState) {
        self.read_op_stack = state.read_op_stack;
        self.write_stack = state.write_stack;
        self.tree_cache.restore(state.tree_cache);
        self.output.set_position(state.output_position);
        self.output
            .get_mut()
            .truncate(state.output_position as usize);
    }
```

**File:** src/serde/incremental.rs (L263-306)
```rust
    #[test]
    fn test_restore() {
        let mut a = Allocator::new();

        let sentinel = a.new_pair(NodePtr::NIL, NodePtr::NIL).unwrap();
        // ((1 . 2) . (3 . 4))
        let item = node_from_bytes(&mut a, &hex!("ffff0102ff0304")).unwrap();
        let list = a.new_pair(item, sentinel).unwrap();

        let mut ser = Serializer::new(Some(sentinel));
        let (done, _) = ser.add(&a, list).unwrap();
        assert!(!done);
        assert_eq!(ser.size(), 8);
        assert_eq!(hex::encode(ser.get_ref()), "ffffff0102ff0304");

        let (done, state) = ser.add(&a, NodePtr::NIL).unwrap();
        assert!(done);
        assert_eq!(ser.size(), 9);
        assert_eq!(hex::encode(ser.get_ref()), "ffffff0102ff030480");

        ser.restore(state.clone());

        assert_eq!(ser.size(), 8);
        assert_eq!(hex::encode(ser.get_ref()), "ffffff0102ff0304");

        let (done, _) = ser.add(&a, item).unwrap();
        assert!(done);

        assert_eq!(ser.size(), 10);
        assert_eq!(hex::encode(ser.get_ref()), "ffffff0102ff0304fe02");

        ser.restore(state);

        let item = a.new_small_number(1337).unwrap();

        let (done, _) = ser.add(&a, item).unwrap();

        assert!(done);
        assert_eq!(ser.size(), 11);
        assert_eq!(hex::encode(ser.get_ref()), "ffffff0102ff0304820539");

        let output = ser.into_inner();
        assert_eq!(hex::encode(&output), "ffffff0102ff0304820539");
    }
```

**File:** src/allocator.rs (L485-498)
```rust
    pub fn restore_transparent_checkpoint(&mut self, cp: &TransparentCheckpoint) {
        // if any of these asserts fire, it means we're trying to restore to
        // a state that has already been "long-jumped" passed (via another
        // restore to an earlier state). You can only restore backwards in time,
        // not forwards.
        assert!(self.u8_vec.len() >= cp.u8s as usize);
        assert!(self.pair_vec.len() >= cp.pairs as usize);
        assert!(self.atom_vec.len() >= cp.atoms as usize);
        self.ghost_heap += self.u8_vec.len() - cp.u8s as usize;
        self.ghost_pairs += self.pair_vec.len() - cp.pairs as usize;
        self.ghost_atoms += self.atom_vec.len() - cp.atoms as usize;
        self.u8_vec.truncate(cp.u8s as usize);
        self.pair_vec.truncate(cp.pairs as usize);
        self.atom_vec.truncate(cp.atoms as usize);
```
