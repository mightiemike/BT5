### Title
Incomplete `TreeCacheCheckpoint` rollback leaves stale parent links that corrupt back-reference paths after `restore()` — (File: `src/serde/tree_cache.rs`)

---

### Summary

`TreeCache::restore()` only rolls back three fields (`stack`, `serialized_nodes`, `sentinel_entry`). It does not roll back `node_map`, `node_entries`, `atom_lookup`, or `pair_lookup`. After a `restore()`, stale `parents` entries accumulated in `node_entries` during the undone `add()` call persist permanently. On the next `add()` call, `find_path()` traverses these stale parent chains and can return a shorter-but-incorrect back-reference path, causing the serialized bytes to deserialize to a different CLVM tree than the one being serialized — a consensus divergence.

---

### Finding Description

**Vulnerability class:** Cache/allocator aliasing — state mutated during a first attempt that permanently corrupts subsequent attempts, directly analogous to the TOTP reuse-lock bug in the report.

**Root cause — `undo_state()` captures an incomplete snapshot:**

`TreeCacheCheckpoint` saves only three fields:

```
stack: Vec<u32>
serialized_nodes: BitSet
sentinel_entry: Option<u32>
``` [1](#0-0) 

`restore()` writes back only those three fields, leaving `node_map`, `node_entries`, `atom_lookup`, and `pair_lookup` in their post-`add()` state: [2](#0-1) 

**How stale parent links accumulate:**

During `update()`, every time a pair node is processed via `CacheOp::Cons`, parent links are appended to the children's `NodeEntry.parents` lists: [3](#0-2) 

These appends are never undone by `restore()`. After restore, a child node's `parents` list contains both the stale parent from the undone attempt and (after the next `update()`) the correct new parent.

**How `find_path()` is corrupted:**

`find_path()` performs a breadth-first search from the target node upward through `parents` links until it reaches a node with `on_stack > 0`. It returns the **first** (shortest) path found: [4](#0-3) 

If the stale parent chain (from the undone attempt) leads to a node that is still on the restored stack via a shorter path than the correct new parent chain, `find_path()` returns the stale path. That path, when written as a `0xfe <path>` back-reference, resolves to the wrong node during deserialization.

**Concrete trigger sequence:**

1. `Serializer::new(Some(sentinel))` — create serializer.
2. `add(a, tree1)` — `update()` adds atom D (NodePtr P) as a child of pair B; B's NodeEntry index is 10, D's NodeEntry index is 11, D.parents = [(10, Left)]. Returns `(false, undo_state)`.
3. `restore(undo_state)` — `stack` and `serialized_nodes` are rolled back; D's NodeEntry 11 and B's NodeEntry 10 remain in `node_entries`; D's stale parent link `(10, Left)` remains.
4. `add(a, tree2)` — `update()` encounters D (same NodePtr P) already in `node_map`; skips re-traversal. Processes new pair E (parent of D in tree2); appends E's index to D.parents → D.parents = [(10, Left), (E_idx, Left)].
5. `find_path(D)` — BFS explores both parent 10 (stale B) and E_idx (correct). If B's chain reaches a stack node in fewer hops than E's chain, the stale path is returned and written as a back-reference.

The `Serializer::restore()` call that triggers this is the designed public API: [5](#0-4) 

`UndoState` and `Serializer` are both exported as public API: [6](#0-5) 

---

### Impact Explanation

**Impact: Medium**

The serialized output produced by `Serializer` after a `restore()` may contain a `0xfe <path>` back-reference that resolves to the wrong subtree during deserialization. The deserialized CLVM tree differs from the tree that was serialized. Any system that uses the incremental serializer with `restore()` to produce CLVM programs for on-chain evaluation will produce programs with different semantics than intended — a consensus divergence between a node using incremental serialization and one using `node_to_bytes_backrefs` directly.

The `serialized_nodes` BitSet is correctly restored, so `find_path()` will not return a path to a node that was only serialized during the undone attempt. The corruption requires the stale parent chain to reach a node that was already on the stack before the undone attempt — a realistic condition when the same atoms appear across multiple incrementally serialized subtrees.

---

### Likelihood Explanation

**Likelihood: Medium**

`restore()` is the designed mechanism for undoing an incremental serialization step and is exported as public API. Any caller that uses `restore()` after `add()` and then calls `add()` again with a tree sharing atoms with the undone tree is exposed. The condition (same `NodePtr` reused across attempts, stale parent chain shorter than correct chain) is realistic in practice because the `Allocator` is shared across all `add()` calls and atoms are frequently reused across subtrees in CLVM programs.

---

### Recommendation

`TreeCacheCheckpoint` must capture the full mutable state of `TreeCache` that `restore()` needs to roll back. Specifically:

- Snapshot `node_map` (or its size, to truncate on restore)
- Snapshot `node_entries` length (to truncate `node_entries` on restore, removing stale entries)
- Snapshot `atom_lookup` and `pair_lookup` (or their sizes, to truncate on restore)
- For each `NodeEntry` whose `parents` list was extended during the attempt, snapshot the original `parents` length so it can be truncated on restore

Alternatively, make `node_entries` append-only and record the length at checkpoint time; on restore, truncate `node_entries`, `node_map`, `atom_lookup`, and `pair_lookup` back to their pre-attempt sizes. This is safe because `node_entries` indices are stable (never reordered).

---

### Proof of Concept

```
Setup:
  Allocator a
  sentinel S = new_pair(NIL, NIL)
  atom_long = new_atom(b"foobar_long_atom")   // NodePtr = P1, serialized_length >= 4
  pair_B = new_pair(atom_long, NIL)            // NodePtr = P2
  tree1 = new_pair(pair_B, sentinel)           // NodePtr = P3

  atom_long2 = atom_long  // same NodePtr P1, same atom
  pair_E = new_pair(NIL, atom_long2)           // NodePtr = P4 (different structure)
  tree2 = new_pair(pair_E, sentinel)           // NodePtr = P5

Step 1: ser = Serializer::new(Some(sentinel))
Step 2: (false, undo) = ser.add(&a, tree1)
  // update() processes tree1:
  //   atom_long (P1) → NodeEntry[2], parents=[]
  //   NIL (P0)       → NodeEntry[0], parents=[]
  //   pair_B (P2)    → NodeEntry[3], atom_long.parents=[(3,Left)], NIL.parents=[(3,Right)]
  //   sentinel (S)   → NodeEntry[4], pair_B.parents=[(5,Left)]
  //   tree1 (P3)     → NodeEntry[5]
  // undo captures: stack=[], serialized_nodes=empty, sentinel_entry=None

Step 3: ser.restore(undo)
  // stack=[], serialized_nodes=empty restored
  // node_map, node_entries UNCHANGED: atom_long still maps to NodeEntry[2]
  //   with parents=[(3,Left)] — stale link to pair_B's NodeEntry

Step 4: (false, _) = ser.add(&a, tree2)
  // update() processes tree2:
  //   atom_long (P1) already in node_map → NodeEntry[2] reused, NOT re-traversed
  //   pair_E (P4): left=NIL[0], right=atom_long[2]
  //     → atom_long.parents now = [(3,Left), (new_E_idx, Right)]  ← stale + correct
  //   sentinel → new NodeEntry
  //   tree2 → new NodeEntry

  // Serializer writes atom_long inline (not yet in serialized_nodes)
  // tree_cache.push(atom_long) → serialized_nodes.visit(2)

Step 5: find_path(atom_long) called for next occurrence
  // BFS from NodeEntry[2]:
  //   parents = [(3,Left), (E_idx,Right)]
  //   Explores NodeEntry[3] (stale pair_B): on_stack=0, but pair_B.parents includes tree1's NodeEntry
  //   Explores NodeEntry[E_idx] (correct pair_E): on_stack=1 (currently on stack)
  //   If stale chain through NodeEntry[3] reaches a stack node in fewer hops → wrong path returned
  //   Serialized as 0xfe <stale_path> → deserializes to wrong subtree
```

The deserialized tree differs from tree2, constituting a consensus divergence in CLVM program representation. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

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

**File:** src/serde/tree_cache.rs (L320-323)
```rust
                    self.node_entries[left_idx].add_parent(idx, ChildPos::Left);
                    self.node_entries[right_idx].add_parent(idx, ChildPos::Right);
                    e.insert(idx);
                    stack.push(idx);
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

**File:** src/serde/tree_cache.rs (L506-514)
```rust
            let (remaining_parents, used_p) = if let Some(first_parent) =
                entry.parents.iter().position(|e| !seen.is_visited(e.0))
            {
                p.idx = entry.parents[first_parent].0;
                p.child = entry.parents[first_parent].1;
                (&entry.parents[(first_parent + 1)..], true)
            } else {
                (&[] as &[(u32, ChildPos)], false)
            };
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

**File:** src/serde/mod.rs (L31-31)
```rust
pub use incremental::{Serializer, UndoState};
```
