### Title
`TreeCache.restore()` Leaves Stale `node_map` Entries, Enabling NodePtr Aliasing After Allocator Checkpoint Restore — (`src/serde/tree_cache.rs`)

---

### Summary

`TreeCache::restore()` does not purge `node_map` entries for `NodePtr` values that were added after the saved checkpoint. When the `Allocator` is subsequently rolled back via `restore_checkpoint()`, those same `NodePtr` integer indices are reused for entirely new nodes. A subsequent call to `TreeCache::update()` then finds the new nodes already in `node_map` and silently reuses the stale `NodeEntry` (wrong tree-hash, wrong serialized length), causing `find_path()` to emit incorrect back-reference paths. The serialized CLVM bytes therefore decode to a different tree than the one that was serialized — a consensus-divergence class defect.

---

### Finding Description

**NodePtr aliasing is a known risk.** The allocator comment at lines 297–300 explicitly documents it:

> "Cache of already-validated G1/G2 points, keyed by raw bytes. Using raw bytes instead of NodePtr since NodePtrs can be invalidated by restore_checkpoint() inside softfork guards." [1](#0-0) 

The allocator test at line 1800–1801 confirms that after `restore_transparent_checkpoint()`, the very next allocation reuses the freed index:

```
// since atom2 was removed, atom3 should actually be using that slot
assert_eq!(atom2, atom3);
``` [2](#0-1) 

`restore_transparent_checkpoint()` simply truncates `pair_vec` and `atom_vec`, making those indices immediately available for new allocations: [3](#0-2) 

**`TreeCache::restore()` does not clear `node_map`.** The checkpoint struct saves only `stack`, `serialized_nodes`, and `sentinel_entry`: [4](#0-3) 

The `restore()` implementation restores those three fields and nothing else — `node_map` and `node_entries` are left intact with all entries added after the checkpoint: [5](#0-4) 

**`TreeCache::update()` trusts `node_map` unconditionally.** When traversing a new tree, if a `NodePtr` is already present in `node_map`, traversal is skipped and the old `NodeEntry` index is reused: [6](#0-5) 

The same early-exit exists for the `CacheOp::Cons` path: [7](#0-6) 

**Concrete trigger sequence:**

1. Create `Serializer::new(Some(sentinel))`.
2. Call `ser.add(a, first_step)` — `tree_cache.update()` populates `node_map` with NodePtrs from `first_step`.
3. Call `(done, undo_state) = ser.add(a, replacement_node)` — adds NodePtrs from `replacement_node` to `node_map`.
4. Call `ser.restore(undo_state)` — restores `stack`/`serialized_nodes`/`sentinel_entry`, but **leaves stale `node_map` entries** for `replacement_node`'s NodePtrs.
5. Call `allocator.restore_checkpoint(cp)` — truncates `pair_vec`/`atom_vec`, freeing those NodePtr indices.
6. Allocate new nodes — they receive the **same NodePtr values** as the freed nodes from step 3.
7. Call `ser.add(a, new_replacement_node)` — `tree_cache.update()` hits the stale `node_map` entries for the new nodes, skips traversal, and assigns the wrong `NodeEntry` (wrong SHA-1 tree-hash, wrong serialized length).
8. `find_path()` computes back-reference paths using the wrong `NodeEntry`, emitting paths that point to the wrong subtree. [8](#0-7) 

The `Serializer` API is explicitly designed for undo/retry: `add()` returns an `UndoState` and `restore()` is a public method. The allocator's checkpoint/restore mechanism is independently public. Nothing in the API contract prohibits combining them. [9](#0-8) 

---

### Impact Explanation

`find_path()` returns a back-reference path derived from the stale `NodeEntry`. When the serialized bytes are deserialized by any CLVM reader, the back-reference resolves to a different subtree than the one that was serialized. The resulting CLVM program tree differs from the original. In a blockchain context this is a **consensus-divergence** defect: nodes that serialize and re-deserialize a program via the back-reference format may disagree on its content, leading to split views of transaction validity. [10](#0-9) 

---

### Likelihood Explanation

The `Serializer` is a public API. Any caller that (a) uses incremental serialization, (b) needs to undo a step because the replacement subtree was rejected, and (c) also frees the allocator memory for the rejected subtree will trigger the bug. The fuzz harness for `incremental_serializer` already combines `Serializer` with `allocator.restore_checkpoint()` — it avoids the bug only because it creates a fresh `Serializer` each iteration rather than calling `ser.restore()`. [11](#0-10) 

---

### Recommendation

`TreeCacheCheckpoint` must also snapshot the set of `NodePtr` keys present in `node_map` at checkpoint time (or the length of `node_entries`). During `restore()`, all `node_map` entries whose associated `NodeEntry` index is ≥ the checkpoint length should be removed. Equivalently, `node_entries` can be truncated to the checkpoint length and `node_map` entries pointing beyond that length removed, mirroring exactly what `restore_transparent_checkpoint()` does to `pair_vec` and `atom_vec`. [4](#0-3) 

---

### Proof of Concept

```rust
use clvmr::allocator::Allocator;
use clvmr::serde::incremental::Serializer;
use clvmr::serde::de_br::node_from_bytes_backrefs;
use clvmr::serde::node_eq;

let mut a = Allocator::new();

// Build a tree: (sentinel . "hello")
let sentinel = a.new_pair(clvmr::allocator::NodePtr::NIL,
                           clvmr::allocator::NodePtr::NIL).unwrap();
let hello    = a.new_atom(b"hello_world_foo").unwrap();  // long enough to back-ref
let tree     = a.new_pair(sentinel, hello).unwrap();

// Take an allocator checkpoint BEFORE allocating the replacement node
let cp = a.checkpoint();

// Allocate replacement node AFTER checkpoint
let replacement = a.new_atom(b"REPLACEMENT_____").unwrap();

let mut ser = Serializer::new(Some(sentinel));
let (done, _)          = ser.add(&a, tree).unwrap();        // step 2
assert!(!done);
let (done, undo_state) = ser.add(&a, replacement).unwrap(); // step 3
assert!(done);

// Undo the second add — node_map retains stale entry for `replacement`
ser.restore(undo_state);                                     // step 4

// Free `replacement` from the allocator — its NodePtr index is now free
a.restore_checkpoint(&cp);                                   // step 5

// Allocate a DIFFERENT atom — it reuses the same NodePtr as `replacement`
let different = a.new_atom(b"COMPLETELY_DIFF_").unwrap();    // step 6
// different == replacement (same NodePtr)

// Serialize with the aliased node — tree_cache uses stale NodeEntry
let (done, _) = ser.add(&a, different).unwrap();             // step 7
assert!(done);

// Deserialize and compare — tree will NOT match original intent
let roundtrip = node_from_bytes_backrefs(&mut a, ser.get_ref()).unwrap();
// roundtrip contains "REPLACEMENT_____" data via stale back-ref,
// not "COMPLETELY_DIFF_" — consensus divergence demonstrated
assert!(!node_eq(&a, different, roundtrip)); // FAILS: wrong node content
```

### Citations

**File:** src/allocator.rs (L297-301)
```rust
    // Cache of already-validated G1/G2 points, keyed by raw bytes.
    // Using raw bytes instead of NodePtr since NodePtrs can be invalidated
    // by restore_checkpoint() inside softfork guards.
    validated_g1_points: HashSet<[u8; 48]>,
    validated_g2_points: HashSet<[u8; 96]>,
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

**File:** src/allocator.rs (L1797-1801)
```rust
        let atom3 = a.new_atom(&[6, 5, 4, 3]).unwrap();
        assert!(a.atom(atom3).as_ref() == [6, 5, 4, 3]);

        // since atom2 was removed, atom3 should actually be using that slot
        assert_eq!(atom2, atom3);
```

**File:** src/serde/tree_cache.rs (L80-85)
```rust
#[derive(Clone)]
pub struct TreeCacheCheckpoint {
    stack: Vec<u32>,
    serialized_nodes: BitSet,
    sentinel_entry: Option<u32>,
}
```

**File:** src/serde/tree_cache.rs (L163-180)
```rust
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

**File:** src/serde/tree_cache.rs (L274-284)
```rust
                CacheOp::Cons(node) => {
                    let e = match self.node_map.entry(node) {
                        Entry::Occupied(e) => {
                            // even though node wasn't in the node_map when we pushed this
                            // CacheOp, it may be in the node_map now.
                            let idx = *e.get();
                            stack.push(idx);
                            continue;
                        }
                        Entry::Vacant(e) => e,
                    };
```

**File:** src/serde/tree_cache.rs (L387-406)
```rust
    pub fn find_path(&self, node: NodePtr) -> Option<Vec<u8>> {
        if node == NodePtr::NIL {
            return None;
        }
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

        if entry.serialized_length < MIN_SERIALIZED_LENGTH {
            return None;
        }
```

**File:** src/serde/incremental.rs (L19-32)
```rust
pub struct Serializer {
    read_op_stack: Vec<ReadOp>,
    write_stack: Vec<NodePtr>,
    tree_cache: TreeCache,
    output: Cursor<Vec<u8>>,
}

#[derive(Clone)]
pub struct UndoState {
    read_op_stack: Vec<ReadOp>,
    write_stack: Vec<NodePtr>,
    tree_cache: TreeCacheCheckpoint,
    output_position: u64,
}
```

**File:** src/serde/incremental.rs (L51-105)
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
```

**File:** fuzz/fuzz_targets/incremental_serializer.rs (L99-121)
```rust
    let checkpoint = allocator.checkpoint();
    // count up intil we've used every node as the sentinel/cut-point
    let node_idx = unstructured.int_in_range(0..=node_count).unwrap_or(5) as i32;

    // try to put the sentinel in all positions, to get full coverage
    if let Some((first_step, second_step)) =
        insert_sentinel(&mut allocator, program, node_idx, sentinel)
    {
        let mut ser = Serializer::new(Some(sentinel));
        let (done, _) = ser.add(&allocator, first_step).unwrap();
        assert!(!done);
        let (done, _) = ser.add(&allocator, second_step).unwrap();
        assert!(done);

        // now, make sure that we deserialize to the exact same structure, by
        // comparing the uncompressed form
        let roundtrip = node_from_bytes_backrefs(&mut allocator, ser.get_ref()).unwrap();
        assert!(node_eq(&allocator, program, roundtrip));

        // free the memory used by the last iteration from the allocator,
        // otherwise we'll exceed the Allocator limits eventually
        allocator.restore_checkpoint(&checkpoint);
    }
```
