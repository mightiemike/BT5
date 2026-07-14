### Title
`Treehasher.sha256_treehash` Ignores Instance Prefixes, Always Uses Module-Level Globals — (File: `wheel/python/clvm_rs/tree_hash.py`)

---

### Summary

The `Treehasher` class is designed to be parameterizable with custom `atom_prefix` and `pair_prefix`. However, the `sha256_treehash` method's inner closures `handle_obj` and `handle_pair` call the **module-level** `shatree_atom` and `shatree_pair` (which are bound to `CHIA_TREEHASHER`'s prefixes `0x01`/`0x02`), rather than `self.shatree_atom` and `self.shatree_pair`. Any `Treehasher` instance initialized with different prefixes silently produces the same hashes as `CHIA_TREEHASHER`. Additionally, the per-object `_cached_sha256_treehash` attribute is always written using the standard Chia prefixes, so a subsequent call from a custom `Treehasher` reads back a stale, prefix-mismatched cached value without recomputing.

---

### Finding Description

In `wheel/python/clvm_rs/tree_hash.py`, `Treehasher.__init__` stores `atom_prefix` and `pair_prefix` as instance attributes and exposes `shatree_atom`/`shatree_pair` methods that use them:

```python
def shatree_atom(self, atom: bytes) -> bytes:
    s = sha256()
    s.update(self.atom_prefix)   # uses self.atom_prefix
    s.update(atom)
    return s.digest()
``` [1](#0-0) 

However, the inner closures of `sha256_treehash` call the **module-level** names `shatree_atom` and `shatree_pair` instead:

```python
elif obj.atom is not None:
    r = shatree_atom(obj.atom)          # module-level, NOT self.shatree_atom
    ...
def handle_pair(...):
    r = shatree_pair(p0, p1)            # module-level, NOT self.shatree_pair
``` [2](#0-1) [3](#0-2) 

At module load time, those module-level names are bound to `CHIA_TREEHASHER`'s methods:

```python
CHIA_TREEHASHER = Treehasher(CHIA_TREE_HASH_ATOM_PREFIX, CHIA_TREE_HASH_PAIR_PREFIX)
sha256_treehash = CHIA_TREEHASHER.sha256_treehash
shatree_atom    = CHIA_TREEHASHER.shatree_atom   # always prefix 0x01
shatree_pair    = CHIA_TREEHASHER.shatree_pair   # always prefix 0x02
``` [4](#0-3) 

Consequently:

1. **Prefix ignored at compute time.** Any `Treehasher` instance with non-standard prefixes will always hash atoms and pairs using `0x01`/`0x02`, not its own configured prefixes.

2. **Cache poisoning across instances.** The computed hash is written to `obj._cached_sha256_treehash` (lines 66, 90). Because the cache key is the object identity (not the prefix), a subsequent call from a different `Treehasher` instance hits the cache at line 57–61 and returns the stale value computed with the wrong prefixes, without recomputing:

```python
r = getattr(obj, "_cached_sha256_treehash", None)
if r is not None:
    self.cache_hits += 1
    hash_stack.append(r)   # returns value computed with CHIA_TREEHASHER's prefixes
    return
``` [5](#0-4) 

This is the direct structural analog to the reported vulnerability: a cached value computed under one set of parameters is returned by a getter that is supposed to use a different (current) set of parameters.

---

### Impact Explanation

**Impact: Medium**

Any caller that constructs a `Treehasher` with non-standard prefixes (e.g., for an alternative hash domain, a test harness, or a future protocol extension) will silently receive hashes computed with the standard Chia `0x01`/`0x02` prefixes. Because `tree_hash()` is the foundation of puzzle-hash derivation and coin-ID computation in the Python API (`Program.tree_hash`, `curry_hash`, `__eq__`), a wrong hash returned here propagates directly into coin-spend validation and puzzle-hash matching. The cache-poisoning variant is particularly dangerous: once a node object has been hashed by `CHIA_TREEHASHER`, every subsequent call from any `Treehasher` instance — regardless of its configured prefixes — returns the stale cached value. [6](#0-5) 

---

### Likelihood Explanation

**Likelihood: Low**

In the current codebase, only `CHIA_TREEHASHER` is instantiated and used. The `Treehasher` class is part of the public Python API, so any downstream library or application that instantiates it with custom prefixes (a plausible use case given the class's explicit parameterization) will trigger the bug. The cache-poisoning path is reachable whenever a Python object is hashed by `CHIA_TREEHASHER` first and then passed to a custom `Treehasher`, which is a realistic API-use pattern.

---

### Recommendation

Replace the bare `shatree_atom` and `shatree_pair` calls inside `sha256_treehash` with `self.shatree_atom` and `self.shatree_pair` so that the instance's configured prefixes are actually used:

```python
# handle_obj: line 63
r = self.shatree_atom(obj.atom)

# handle_pair: line 86
r = self.shatree_pair(p0, p1)
```

Additionally, the `_cached_sha256_treehash` attribute should either be keyed by prefix or not shared across `Treehasher` instances with different configurations.

---

### Proof of Concept

```python
from clvm_rs.tree_hash import Treehasher, CHIA_TREEHASHER, CHIA_TREE_HASH_ATOM_PREFIX, CHIA_TREE_HASH_PAIR_PREFIX

# Custom Treehasher with different prefixes
alt = Treehasher(bytes.fromhex("03"), bytes.fromhex("04"))

class FakeAtom:
    atom = b"hello"
    pair = None

obj = FakeAtom()

chia_hash = CHIA_TREEHASHER.sha256_treehash(obj)
alt_hash  = alt.sha256_treehash(obj)

# Both return the same value — alt's prefixes 0x03/0x04 were never used.
assert chia_hash == alt_hash, "BUG: custom prefixes silently ignored"

# Cache poisoning: obj._cached_sha256_treehash is now set with 0x01 prefix.
# A fresh alt instance also hits the cache and returns the wrong hash.
alt2 = Treehasher(bytes.fromhex("03"), bytes.fromhex("04"))
alt2_hash = alt2.sha256_treehash(obj)
assert alt2_hash == chia_hash, "BUG: stale cached value returned for different prefix"
``` [7](#0-6)

### Citations

**File:** wheel/python/clvm_rs/tree_hash.py (L37-41)
```python
    def shatree_atom(self, atom: bytes) -> bytes:
        s = sha256()
        s.update(self.atom_prefix)
        s.update(atom)
        return s.digest()
```

**File:** wheel/python/clvm_rs/tree_hash.py (L50-100)
```python
    def sha256_treehash(self, clvm_storage: CLVMStorage) -> bytes:
        def handle_obj(
            obj_stack: List[CLVMStorage],
            hash_stack: List[bytes],
            op_stack: List[OP_STACK_F],
        ) -> None:
            obj = obj_stack.pop()
            r = getattr(obj, "_cached_sha256_treehash", None)
            if r is not None:
                self.cache_hits += 1
                hash_stack.append(r)
                return
            elif obj.atom is not None:
                r = shatree_atom(obj.atom)
                hash_stack.append(r)
                try:
                    setattr(obj, "_cached_sha256_treehash", r)
                except AttributeError:
                    pass
            else:
                pair = cast(Tuple[CLVMStorage, CLVMStorage], obj.pair)
                p0, p1 = pair
                obj_stack.append(obj)
                obj_stack.append(p0)
                obj_stack.append(p1)
                op_stack.append(handle_pair)
                op_stack.append(handle_obj)
                op_stack.append(handle_obj)

        def handle_pair(
            obj_stack: List[CLVMStorage],
            hash_stack: List[bytes],
            op_stack: List[OP_STACK_F],
        ) -> None:
            p0 = hash_stack.pop()
            p1 = hash_stack.pop()
            r = shatree_pair(p0, p1)
            hash_stack.append(r)
            obj = obj_stack.pop()
            try:
                setattr(obj, "_cached_sha256_treehash", r)
            except AttributeError:
                pass

        obj_stack: List[CLVMStorage] = [clvm_storage]
        op_stack: List[OP_STACK_F] = [handle_obj]
        hash_stack: List[bytes] = []
        while len(op_stack) > 0:
            op: OP_STACK_F = op_stack.pop()
            op(obj_stack, hash_stack, op_stack)
        return hash_stack[0]
```

**File:** wheel/python/clvm_rs/tree_hash.py (L103-109)
```python
CHIA_TREE_HASH_ATOM_PREFIX = bytes.fromhex("01")
CHIA_TREE_HASH_PAIR_PREFIX = bytes.fromhex("02")
CHIA_TREEHASHER = Treehasher(CHIA_TREE_HASH_ATOM_PREFIX, CHIA_TREE_HASH_PAIR_PREFIX)

sha256_treehash = CHIA_TREEHASHER.sha256_treehash
shatree_atom = CHIA_TREEHASHER.shatree_atom
shatree_pair = CHIA_TREEHASHER.shatree_pair
```

**File:** wheel/python/clvm_rs/program.py (L281-286)
```python
    def tree_hash(self) -> bytes:
        # we operate on the unwrapped version to prevent the re-wrapping that
        # happens on each invocation of `Program.pair` whenever possible
        if self._cached_sha256_treehash is None:
            self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
        return self._cached_sha256_treehash
```
