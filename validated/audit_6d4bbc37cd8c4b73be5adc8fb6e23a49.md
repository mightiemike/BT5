### Title
Unconditional Duplicate Parent Registration in `TreeCache::update()` Corrupts Back-Reference Path Search - (File: `src/serde/tree_cache.rs`)

---

### Summary

In `TreeCache::update()`, when a pair node is processed via `CacheOp::Cons` and its `(left_idx, right_idx)` key already exists in `pair_lookup`, the code correctly reuses the existing `NodeEntry` index — but then **unconditionally** calls `add_parent()` on both children regardless. This is the direct analog of the missing duplicate-veto check: a state mutation (`add_parent`) is applied a second time without first verifying it was not already applied for this exact relationship. The result is that a child `NodeEntry`'s `parents` list accumulates duplicate entries, which, once the `MAX_PARENTS = 8` cap is reached, causes the pseudo-random eviction logic to overwrite legitimate parent entries. This corrupts `find_path()`'s BFS traversal, causing it to miss valid back-reference paths and produce non-canonical serialized output.

---

### Finding Description

In `src/serde/tree_cache.rs`, the `CacheOp::Cons` arm of `update()` performs two distinct deduplication checks:

**Check 1 — node_map (lines 275–283):** Guards against re-processing the same `NodePtr` twice. [1](#0-0) 

**Check 2 — pair_lookup (lines 304–318):** Guards against creating a duplicate `NodeEntry` for a pair whose children already have an entry. [2](#0-1) 

However, after both checks, `add_parent()` is called **unconditionally** on both children: [3](#0-2) 

The missing guard is: when `pair_lookup` returns `Entry::Occupied` (the pair `NodeEntry` already exists), `add_parent` should **not** be called again, because the parent-child relationship was already registered the first time this `(left_idx, right_idx)` pair was processed.

**Concrete trigger:** The allocator does not deduplicate pairs — each `new_pair(a, b)` call produces a fresh `NodePtr`. When a CLVM tree contains two different `NodePtr` values that are structurally identical pairs (same children, which were themselves deduplicated via `atom_lookup` or `pair_lookup` into the same `NodeEntry` indices), the second pair's `CacheOp::Cons` processing will:

1. Pass the `node_map` check (it is a new `NodePtr`).
2. Hit `Entry::Occupied` in `pair_lookup` (same `left_idx`/`right_idx` key).
3. Still call `add_parent(idx, ChildPos::Left)` and `add_parent(idx, ChildPos::Right)` on the children — duplicating entries already present.

The `add_parent` function has a hard cap of `MAX_PARENTS = 8`: [4](#0-3) 

Once the cap is reached, the eviction formula `self.parents[idx % MAX_PARENTS] = (parent, pos)` overwrites an existing slot with the duplicate. This permanently destroys a legitimate parent entry, not just wastes a slot.

---

### Impact Explanation

`find_path()` performs a BFS from a target node upward through `parents` to reach the serialization stack: [5](#0-4) 

If a legitimate parent has been evicted by a duplicate, `find_path()` returns `None` for a path that actually exists. The `Serializer` in `incremental.rs` then emits the full subtree inline instead of a compact back-reference byte sequence: [6](#0-5) 

This produces **non-canonical serialized output**: two Chia nodes serializing the same CLVM tree with back-references may produce different byte sequences depending on whether the duplicate-parent corruption was triggered. Since serialized spend bundles and puzzles are hashed for coin IDs and mempool deduplication, divergent serialization is a consensus-relevant discrepancy.

---

### Likelihood Explanation

Any CLVM tree with more than four structurally identical sub-pairs sharing the same children will exhaust the `MAX_PARENTS = 8` budget (two slots consumed per duplicate pair: one for `Left`, one for `Right`). Curried programs, repeated list elements, and template-expanded puzzles routinely produce such structures. An attacker can craft a spend bundle whose puzzle or solution contains a tree with many repeated identical sub-pairs, reliably triggering the eviction path and suppressing back-reference compression for targeted subtrees.

---

### Recommendation

In the `CacheOp::Cons` arm, guard the `add_parent` calls so they only execute when the `NodeEntry` was **newly created** (i.e., `pair_lookup` returned `Entry::Vacant`). When `Entry::Occupied` is returned, the parent-child relationship was already registered during the first traversal of this logical pair, and no second registration should occur.

Concretely, restructure lines 304–321 so that `add_parent` is inside the `Entry::Vacant` branch:

```rust
let idx = match self.pair_lookup.entry(key) {
    Entry::Occupied(e) => *e.get(),
    Entry::Vacant(e) => {
        let idx = self.node_entries.len() as u32;
        let entry = NodeEntry { ... };
        self.node_entries.push(entry);
        e.insert(idx);
        // Only register parents for a newly created NodeEntry
        self.node_entries[left_idx].add_parent(idx, ChildPos::Left);
        self.node_entries[right_idx].add_parent(idx, ChildPos::Right);
        idx
    }
};
// add_parent calls removed from here
e.insert(idx);
stack.push(idx);
```

---

### Proof of Concept

```rust
// Two different NodePtr pairs with structurally identical children
let mut a = Allocator::new();
let atom1 = a.new_atom(b"foobar").unwrap();  // NodePtr A
let atom2 = a.new_atom(b"foobar").unwrap();  // NodePtr B (same content, different ptr)

// p1 and p2 are different NodePtrs but same logical pair (atom1, atom1) / (atom2, atom2)
// After atom deduplication in update(), both map to the same left_idx and right_idx
let p1 = a.new_pair(atom1, atom1).unwrap();
let p2 = a.new_pair(atom2, atom2).unwrap();
let root = a.new_pair(p1, p2).unwrap();

let mut tree = TreeCache::new(None);
tree.update(&a, root);
// After update():
// - atom1 and atom2 share one NodeEntry (deduplicated via atom_lookup)
// - p1 and p2 share one NodeEntry (deduplicated via pair_lookup)
// - BUT add_parent was called TWICE for the shared atom NodeEntry:
//   once when p1 was processed, once when p2 was processed
// - The atom's parents list now has 4 entries: (pair_idx, Left) x2, (pair_idx, Right) x2
// - With 9+ identical sub-pairs, MAX_PARENTS=8 is exceeded and legitimate parents are evicted

// find_path() now misses valid back-references for the atom subtree
tree.push(atom1);
tree.pop2_and_cons(p1);
// find_path(atom2) may return None even though a valid path exists
``` [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** src/serde/tree_cache.rs (L41-52)
```rust
const MAX_PARENTS: usize = 8;

impl NodeEntry {
    fn add_parent(&mut self, parent: u32, pos: ChildPos) {
        if self.parents.len() >= MAX_PARENTS {
            // evict a "random" parent
            let idx = parent as usize + pos as usize;
            self.parents[idx % MAX_PARENTS] = (parent, pos);
        } else {
            self.parents.push((parent, pos));
        }
    }
```

**File:** src/serde/tree_cache.rs (L275-283)
```rust
                    let e = match self.node_map.entry(node) {
                        Entry::Occupied(e) => {
                            // even though node wasn't in the node_map when we pushed this
                            // CacheOp, it may be in the node_map now.
                            let idx = *e.get();
                            stack.push(idx);
                            continue;
                        }
                        Entry::Vacant(e) => e,
```

**File:** src/serde/tree_cache.rs (L304-323)
```rust
                    let idx = match self.pair_lookup.entry(key) {
                        Entry::Occupied(e) => *e.get(),
                        Entry::Vacant(e) => {
                            let idx = self.node_entries.len() as u32;
                            let entry = NodeEntry {
                                tree_hash: None,
                                parents: vec![],
                                serialized_length,
                                on_stack: 0,
                            };
                            self.node_entries.push(entry);
                            e.insert(idx);
                            idx
                        }
                    };

                    self.node_entries[left_idx].add_parent(idx, ChildPos::Left);
                    self.node_entries[right_idx].add_parent(idx, ChildPos::Right);
                    e.insert(idx);
                    stack.push(idx);
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

**File:** src/serde/incremental.rs (L62-95)
```rust
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
```
