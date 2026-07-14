### Title
Unbounded `parent_lookup` Vec Growth in `ReadCacheLookup` Causes O(N²) Serialization CPU Exhaustion — (`File: src/serde/read_cache_lookup.rs`)

### Summary

`ReadCacheLookup`, used by `node_to_bytes_backrefs` during compressed CLVM serialization, maintains a `parent_lookup: HashMap<Bytes32, Vec<(Bytes32, bool)>>` whose per-hash `Vec` grows without any cap. Every `push()` and `pop2_and_cons()` call appends new entries; `pop()` only decrements a reference count but never removes stale entries from `parent_lookup`. A CLVM tree with N identical atoms causes `parent_lookup[hash]` to accumulate O(N) entries. Because `find_paths()` iterates over every entry in that `Vec` on each call — including stale ones — the total serialization cost becomes O(N²), enabling CPU exhaustion. The newer `TreeCache`-based `Serializer` explicitly caps parents at `MAX_PARENTS = 8`; `ReadCacheLookup` has no equivalent guard.

### Finding Description

**Root cause — unbounded append, no eviction:**

Every call to `push()` unconditionally appends to two `Vec`s inside `parent_lookup`:

```rust
self.parent_lookup.entry(id).or_default().push(new_parent_to_old_root);   // line 77-80
self.parent_lookup.entry(self.root_hash).or_default().push(new_parent_to_id); // line 83-86
``` [1](#0-0) 

`pop()` decrements `count` but leaves `parent_lookup` untouched:

```rust
fn pop(&mut self) -> (Bytes32, Bytes32) {
    let item = self.read_stack.pop().expect("stack empty");
    *self.count.entry(item.0).or_insert(0) -= 1;
    *self.count.entry(self.root_hash).or_insert(0) -= 1;
    self.root_hash = item.1;
    item
}
``` [2](#0-1) 

`pop2_and_cons()` similarly appends two more entries per call: [3](#0-2) 

**Stale entries iterated on every `find_paths()` call:**

`find_paths()` iterates over the entire `Vec` for a given hash, including all stale entries where `count == 0`:

```rust
for (parent, direction) in items.iter() {
    if *(self.count.get(parent).unwrap_or(&0)) > 0 && !seen_ids.contains(parent) {
        // only live entries produce work, but ALL entries are iterated
    }
    seen_ids.insert(parent);
}
``` [4](#0-3) 

**Contrast with `TreeCache` which caps at `MAX_PARENTS = 8`:**

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
``` [5](#0-4) 

`ReadCacheLookup` has no equivalent guard.

**Serializer entry point:**

`node_to_bytes_backrefs` → `node_to_stream_backrefs` → `ReadCacheLookup::push()` / `pop2_and_cons()` on every node: [6](#0-5) 

### Impact Explanation

For a CLVM tree with N identical atoms (same byte content, distinct `NodePtr`s), each serialization step pushes the same tree hash, causing `parent_lookup[hash]` to grow by 1 per push. After K pushes of the same hash, `find_paths()` iterates K entries. Summing over all N occurrences: 1 + 2 + … + N = O(N²) total iterations. At the Allocator's atom/pair limits, this produces a sustained CPU spike during `node_to_bytes_backrefs` with no cost-model guard. Any Chia node that serializes attacker-supplied CLVM programs with back-reference compression (e.g., for mempool propagation or storage) is exposed.

### Likelihood Explanation

An attacker submits a spend bundle whose puzzle or solution is a CLVM tree composed of many identical large atoms (≥ 4 bytes, to pass the `serialized_length < 4` early-exit guard). The node deserializes it into an `Allocator`, then calls `node_to_bytes_backrefs` to re-serialize it for propagation. No privileged access is required; only a valid transaction submission is needed. The Allocator's `MAX_NUM_ATOMS` / `MAX_NUM_PAIRS` limits bound N, but even at moderate N the quadratic cost is significant.

### Recommendation

Cap the `Vec` size per hash entry in `parent_lookup`, mirroring `TreeCache`'s `MAX_PARENTS = 8`:

```rust
const MAX_PARENT_ENTRIES: usize = 8;

fn push_parent(lookup: &mut HashMap<Bytes32, Vec<(Bytes32, bool)>>, key: Bytes32, val: (Bytes32, bool)) {
    let v = lookup.entry(key).or_default();
    if v.len() < MAX_PARENT_ENTRIES {
        v.push(val);
    }
    // else: silently drop; find_paths() degrades gracefully to fewer paths
}
```

Alternatively, remove entries from `parent_lookup` when their `count` drops to zero inside `pop()`.

### Proof of Concept

```rust
use clvmr::allocator::Allocator;
use clvmr::serde::node_to_bytes_backrefs;

fn main() {
    let mut a = Allocator::new();
    // Build a right-spine list of N identical 6-byte atoms.
    // Each atom has the same tree hash, so parent_lookup[hash] grows to N.
    let n = 10_000usize;
    let atom = a.new_atom(b"foobar").unwrap(); // serialized_length = 7 >= 4
    let mut node = a.new_pair(atom, clvmr::allocator::NodePtr::NIL).unwrap();
    for _ in 1..n {
        let a2 = a.new_atom(b"foobar").unwrap(); // distinct NodePtr, same hash
        node = a.new_pair(a2, node).unwrap();
    }
    // This call triggers O(N^2) iterations inside ReadCacheLookup::find_paths()
    let _ = node_to_bytes_backrefs(&a, node).unwrap();
}
```

Each of the N atoms shares the same SHA-256 tree hash. During serialization, `parent_lookup[hash(foobar)]` accumulates N entries. The K-th call to `find_paths(hash(foobar))` iterates K stale entries, yielding O(N²) total work with no cost-model enforcement.

### Citations

**File:** src/serde/read_cache_lookup.rs (L65-88)
```rust
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
```

**File:** src/serde/read_cache_lookup.rs (L94-100)
```rust
    fn pop(&mut self) -> (Bytes32, Bytes32) {
        let item = self.read_stack.pop().expect("stack empty");
        *self.count.entry(item.0).or_insert(0) -= 1;
        *self.count.entry(self.root_hash).or_insert(0) -= 1;
        self.root_hash = item.1;
        item
    }
```

**File:** src/serde/read_cache_lookup.rs (L104-125)
```rust
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

**File:** src/serde/read_cache_lookup.rs (L171-187)
```rust
                let parents = self.parent_lookup.get(node);
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
