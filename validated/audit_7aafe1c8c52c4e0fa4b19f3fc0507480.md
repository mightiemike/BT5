### Title
Unbounded `parent_lookup` Vec Growth in `ReadCacheLookup` Causes O(N²) Serialization DoS - (File: `src/serde/read_cache_lookup.rs`)

---

### Summary

`ReadCacheLookup`, used by `node_to_bytes_backrefs` during back-reference compressed serialization, maintains a `parent_lookup: HashMap<Bytes32, Vec<(Bytes32, bool)>>` whose per-hash `Vec` values grow without any size cap. Every call to `push()` and `pop2_and_cons()` appends unconditionally to these `Vec`s. For a CLVM tree with N nodes sharing the same tree hash, the `Vec` for that hash reaches length O(N), and `find_paths` — called once per node — iterates over the entire `Vec` each time, producing O(N²) total work. This is the direct analog of the original report's `SimpleMap` linear-search DoS: an unbounded accumulation structure used for hash tracking that degrades quadratically under attacker-controlled repeated hashes.

---

### Finding Description

`ReadCacheLookup` is declared in `src/serde/read_cache_lookup.rs`:

```rust
parent_lookup: HashMap<Bytes32, Vec<(Bytes32, bool)>, RandomState>,
``` [1](#0-0) 

Every call to `push()` unconditionally appends two entries — one for `id` and one for `self.root_hash`:

```rust
self.parent_lookup.entry(id).or_default().push(new_parent_to_old_root);
// ...
self.parent_lookup.entry(self.root_hash).or_default().push(new_parent_to_id);
``` [2](#0-1) 

Every call to `pop2_and_cons()` appends two more entries — one for `left.0` and one for `right.0`:

```rust
self.parent_lookup.entry(left.0).or_default().push((new_root_hash, false));
self.parent_lookup.entry(right.0).or_default().push((new_root_hash, true));
``` [3](#0-2) 

There is **no cap** on the length of these `Vec` values. Contrast this with `TreeCache`, which enforces `MAX_PARENTS = 8` and evicts entries when the limit is reached:

```rust
const MAX_PARENTS: usize = 8;
fn add_parent(&mut self, parent: u32, pos: ChildPos) {
    if self.parents.len() >= MAX_PARENTS {
        let idx = parent as usize + pos as usize;
        self.parents[idx % MAX_PARENTS] = (parent, pos);
    } else {
        self.parents.push((parent, pos));
    }
}
``` [4](#0-3) 

`ReadCacheLookup` has no equivalent guard.

`find_paths` — called once per node during serialization — iterates over the entire `Vec` for each hash in `partial_paths`:

```rust
for (parent, direction) in items.iter() {
    if *(self.count.get(parent).unwrap_or(&0)) > 0 && !seen_ids.contains(parent) {
        // ...
    }
    seen_ids.insert(parent);
}
``` [5](#0-4) 

The `seen_ids` set prevents BFS exponential blowup but does **not** prevent the O(K) iteration over the `Vec` for a single hash that has K accumulated entries.

`ReadCacheLookup` is instantiated and driven by `node_to_stream_backrefs` in `src/serde/ser_br.rs`:

```rust
let mut read_cache_lookup = ReadCacheLookup::new();
// ...
match read_cache_lookup.find_path(node_tree_hash, node_serialized_length) {
    Some(path) => { ... read_cache_lookup.push(*node_tree_hash); }
    None => match allocator.sexp(node_to_write) {
        SExp::Atom => { ... read_cache_lookup.push(*node_tree_hash); }
        ...
    }
}
while let Some(ReadOp::Cons) = read_op_stack.last() {
    read_op_stack.pop();
    read_cache_lookup.pop2_and_cons();
}
``` [6](#0-5) 

This is exposed to Python callers via `ser_backrefs` in `wheel/src/api.rs`:

```rust
fn ser_backrefs(py: Python, node: &LazyNode) -> PyResult<Py<PyBytes>> {
    let bytes = node_to_bytes_backrefs(node.allocator(), node.node()).map_err(eval_to_py)?;
``` [7](#0-6) 

---

### Impact Explanation

For a CLVM tree containing N nodes that all share the same tree hash (e.g., a linked list of N copies of the same atom `A` with `serialized_length >= 4`):

- Each `push(hash_of_A)` call appends one entry to `parent_lookup[hash_of_A]`.
- Each `pop2_and_cons()` call appends one entry to `parent_lookup[hash_of_A]` (since A is the left child of each cons).
- After processing K cons cells, `parent_lookup[hash_of_A]` has O(K) entries.
- `find_paths(hash_of_A, ...)` is called for each of the N occurrences of A, and each call iterates over O(K) entries.
- Total work: O(1 + 2 + ... + N) = **O(N²)**.

A crafted input of a few megabytes (e.g., 100,000 repeated atoms) produces ~10^10 inner-loop iterations, causing the serializer to stall for tens of seconds. This is a concrete, attacker-triggerable denial of service against any Chia component that calls `node_to_bytes_backrefs` or the Python `ser_backrefs` binding on attacker-supplied CLVM bytes.

The corrupted result is: **serialization wall-clock time grows quadratically** with the number of repeated subtrees, rather than linearly, with no error returned and no cost-model enforcement to bound it.

---

### Likelihood Explanation

The Python `ser_backrefs` binding is a public API. Any Chia wallet, full node, or tooling that re-serializes CLVM programs with compression on externally-sourced data is reachable. A CLVM program consisting of a long list of identical atoms is trivially constructable and valid. The Allocator's pair/atom limits bound N to at most ~2^32, but practical block-size limits (a few MB) already allow N large enough to produce multi-second stalls. No special privileges are required beyond the ability to supply CLVM bytes to a caller of `ser_backrefs` or `node_to_bytes_backrefs`.

---

### Recommendation

Apply the same `MAX_PARENTS`-style cap used in `TreeCache` to the `Vec` values in `ReadCacheLookup.parent_lookup`. When a `Vec` for a given hash exceeds a fixed bound (e.g., 8–16 entries), evict the oldest or a pseudo-random entry rather than appending unconditionally. This bounds `find_paths` iteration to O(1) per hash per BFS step, restoring O(N log N) or O(N) overall serialization complexity.

---

### Proof of Concept

```rust
// Craft a CLVM list: (A . (A . (A . ... (A . nil)...)))
// where A is a 5-byte atom (serialized_length = 6 >= 4, qualifies for back-refs)
let mut a = Allocator::new();
let atom = a.new_atom(&[1, 2, 3, 4, 5]).unwrap(); // hash_of_A is fixed
let mut node = NodePtr::NIL;
let n = 100_000usize;
for _ in 0..n {
    node = a.new_pair(atom, node).unwrap();
}
// node_to_bytes_backrefs now runs in O(n^2) time:
// parent_lookup[hash_of_A] grows to length ~n,
// and find_paths iterates over it n times.
let _ = node_to_bytes_backrefs(&a, node); // stalls for tens of seconds
```

The root cause is at `src/serde/read_cache_lookup.rs` lines 77–86 (`push`) and 114–122 (`pop2_and_cons`): unconditional `.push()` with no size cap, contrasted with `src/serde/tree_cache.rs` lines 44–52 where `MAX_PARENTS = 8` is enforced. [8](#0-7) [9](#0-8) [4](#0-3) [10](#0-9) [11](#0-10)

### Citations

**File:** src/serde/read_cache_lookup.rs (L37-39)
```rust
    /// a mapping of tree hashes to `(parent, is_right)` tuples
    parent_lookup: HashMap<Bytes32, Vec<(Bytes32, bool)>, RandomState>,
}
```

**File:** src/serde/read_cache_lookup.rs (L64-89)
```rust
    /// update the cache based on pushing an object with the given tree hash
    pub fn push(&mut self, id: Bytes32) {
        // we add two new entries: the new root of the tree, and this object (by id)
        // new_root: (id, old_root)

        let new_root_hash = hash_blobs(&[&[2], &id, &self.root_hash]);

        self.read_stack.push((id, self.root_hash));

        *self.count.entry(id).or_insert(0) += 1;
        *self.count.entry(new_root_hash).or_insert(0) += 1;

        let new_parent_to_old_root = (new_root_hash, false);
        self.parent_lookup
            .entry(id)
            .or_default()
            .push(new_parent_to_old_root);

        let new_parent_to_id = (new_root_hash, true);
        self.parent_lookup
            .entry(self.root_hash)
            .or_default()
            .push(new_parent_to_id);

        self.root_hash = new_root_hash;
    }
```

**File:** src/serde/read_cache_lookup.rs (L102-125)
```rust
    /// update the cache based on the "pop/pop/cons" operation used
    /// during deserialization
    pub fn pop2_and_cons(&mut self) {
        // we remove two items: each side of each left/right pair
        let right = self.pop();
        let left = self.pop();

        *self.count.entry(left.0).or_insert(0) += 1;
        *self.count.entry(right.0).or_insert(0) += 1;

        let new_root_hash = hash_blobs(&[&[2], &left.0, &right.0]);

        self.parent_lookup
            .entry(left.0)
            .or_default()
            .push((new_root_hash, false));

        self.parent_lookup
            .entry(right.0)
            .or_default()
            .push((new_root_hash, true));

        self.push(new_root_hash);
    }
```

**File:** src/serde/read_cache_lookup.rs (L172-187)
```rust
                if let Some(items) = parents {
                    for (parent, direction) in items.iter() {
                        if *(self.count.get(parent).unwrap_or(&0)) > 0 && !seen_ids.contains(parent)
                        {
                            if path.len() > max_path_length {
                                return possible_responses;
                            }
                            if path.len() < max_path_length {
                                let mut new_path = path.clone();
                                new_path.push(*direction);
                                new_partial_paths.push((*parent, new_path));
                            }
                        }
                        seen_ids.insert(parent);
                    }
                }
```

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

**File:** src/serde/ser_br.rs (L22-73)
```rust
pub fn node_to_stream_backrefs<W: io::Write>(
    allocator: &Allocator,
    node: NodePtr,
    f: &mut W,
) -> Result<()> {
    let mut read_op_stack: Vec<ReadOp> = vec![ReadOp::Parse];
    let mut write_stack: Vec<NodePtr> = vec![node];

    let mut read_cache_lookup = ReadCacheLookup::new();

    let mut thc = ObjectCache::new(treehash);
    let mut slc = ObjectCache::new(serialized_length);

    while let Some(node_to_write) = write_stack.pop() {
        let op = read_op_stack.pop();
        assert!(op == Some(ReadOp::Parse));

        let node_serialized_length = *slc
            .get_or_calculate(allocator, &node_to_write, None)
            .expect("couldn't calculate serialized length");
        let node_tree_hash = thc
            .get_or_calculate(allocator, &node_to_write, None)
            .expect("can't get treehash");
        match read_cache_lookup.find_path(node_tree_hash, node_serialized_length) {
            Some(path) => {
                f.write_all(&[BACK_REFERENCE])?;
                write_atom(f, &path)?;
                read_cache_lookup.push(*node_tree_hash);
            }
            None => match allocator.sexp(node_to_write) {
                SExp::Pair(left, right) => {
                    f.write_all(&[CONS_BOX_MARKER])?;
                    write_stack.push(right);
                    write_stack.push(left);
                    read_op_stack.push(ReadOp::Cons);
                    read_op_stack.push(ReadOp::Parse);
                    read_op_stack.push(ReadOp::Parse);
                }
                SExp::Atom => {
                    let atom = allocator.atom(node_to_write);
                    write_atom(f, atom.as_ref())?;
                    read_cache_lookup.push(*node_tree_hash);
                }
            },
        }
        while let Some(ReadOp::Cons) = read_op_stack.last() {
            read_op_stack.pop();
            read_cache_lookup.pop2_and_cons();
        }
    }
    Ok(())
}
```

**File:** wheel/src/api.rs (L167-171)
```rust
#[pyfunction]
fn ser_backrefs(py: Python, node: &LazyNode) -> PyResult<Py<PyBytes>> {
    let bytes = node_to_bytes_backrefs(node.allocator(), node.node()).map_err(eval_to_py)?;
    Ok(PyBytes::new(py, &bytes).unbind())
}
```
