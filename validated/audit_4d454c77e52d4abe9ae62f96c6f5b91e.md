### Title
Monotonically Growing `parent_lookup` Vec in `ReadCacheLookup` Causes Quadratic-Cost `find_paths()` Traversal — (File: `src/serde/read_cache_lookup.rs`)

---

### Summary

`ReadCacheLookup::parent_lookup` accumulates parent-edge entries on every `push()` and `pop2_and_cons()` call but **never removes them** when `pop()` is called. `find_paths()` iterates over every accumulated entry — including stale, zero-count ones — for each lookup. When an attacker-controlled CLVM tree contains many nodes sharing the same tree hash, the per-lookup iteration cost grows linearly with the number of prior insertions, producing O(K²) total work for K occurrences of the same hash.

---

### Finding Description

`ReadCacheLookup` is the data structure used during back-reference serialization (`node_to_bytes_backrefs`) to simulate the reader's parse stack so the writer can locate the shortest back-reference path to any already-serialized sub-tree.

**Monotonically growing `parent_lookup`**

Every call to `push()` appends two new `(parent_hash, direction)` tuples into `parent_lookup`: [1](#0-0) 

Every call to `pop2_and_cons()` appends two more tuples: [2](#0-1) 

The `pop()` method only decrements the `count` map — it **never removes entries from `parent_lookup`**: [3](#0-2) 

So `parent_lookup[hash]` is a write-only, ever-growing `Vec`. Entries whose corresponding `count` has dropped to zero are logically dead but physically remain in the vector forever.

**Stale entries iterated on every `find_paths()` call**

`find_paths()` iterates over the full `Vec` for the target hash, checking `count > 0` to skip dead entries — but the dead entries are still traversed: [4](#0-3) 

If the same tree hash `H` has been pushed and popped K times, `parent_lookup[H]` contains O(K) entries. Each call to `find_paths(H, …)` iterates all O(K) entries. Because `find_paths` is called once per serialized node, K calls × O(K) entries = **O(K²) total work** for that single hash.

---

### Impact Explanation

An attacker who can influence the CLVM tree fed to `node_to_bytes_backrefs` (e.g., by submitting a block generator or puzzle reveal containing many occurrences of the same atom or sub-tree) can force the serializer into quadratic time. For a tree with N total nodes where a single atom hash appears K = O(N) times, serialization degrades from O(N) to O(N²). At N = 50 000 nodes (well within a Chia block generator), this is 2.5 × 10⁹ inner-loop iterations — a practical denial-of-service against any full node that serializes the generator with back-references.

The corrupted result is **wall-clock time**: the serializer stalls proportionally to the square of the repeated-hash count, with no CLVM cost-model gate to bound it, because `ReadCacheLookup` operates entirely outside the VM execution loop.

---

### Likelihood Explanation

Moderate-to-high. Chia block generators routinely contain thousands of identical atoms (e.g., the nil terminator `0x`, small integers, repeated puzzle hashes). An adversary can deliberately craft a spend bundle whose puzzle reveals share a maximally repeated atom, amplifying the effect. No special privilege is required beyond the ability to submit a transaction to the mempool.

---

### Recommendation

Track the high-water-mark index of the first live entry per hash (analogous to the counter variable in the report's recommendation), or remove dead entries from `parent_lookup[hash]` inside `pop()`. A minimal fix is to record, alongside each `Vec`, the index of the first entry whose `count` is still positive, and start iteration there in `find_paths()`. Alternatively, replace the `Vec` with a structure that supports O(1) removal of dead entries (e.g., a `VecDeque` with a front-pointer, since entries are logically retired in FIFO order as the stack unwinds).

---

### Proof of Concept

```
# Craft a CLVM tree: a deeply nested list of N copies of atom 0x01
# (a . (a . (a . ... (a . ()) ...)))
# Every node shares hash(0x01).

import clvmr
from clvmr.serde import node_to_bytes_backrefs

a = clvmr.Allocator()
node = a.nil()
N = 20_000
atom = a.new_atom(b'\x01')
for _ in range(N):
    node = a.new_pair(atom, node)   # atom appears N times

# Triggers ReadCacheLookup.push() N times for hash(0x01).

### Citations

**File:** src/serde/read_cache_lookup.rs (L76-88)
```rust
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

**File:** src/serde/read_cache_lookup.rs (L113-124)
```rust

        self.parent_lookup
            .entry(left.0)
            .or_default()
            .push((new_root_hash, false));

        self.parent_lookup
            .entry(right.0)
            .or_default()
            .push((new_root_hash, true));

        self.push(new_root_hash);
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
