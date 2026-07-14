### Title
Stale `_cached_sha256_treehash` Trusted Without Verification Yields Wrong Puzzle Hash — (`wheel/python/clvm_rs/program.py`, `wheel/python/clvm_rs/tree_hash.py`)

---

### Summary

The Python `Program.tree_hash()` method and `Treehasher.sha256_treehash()` unconditionally trust the `_cached_sha256_treehash` attribute on any `CLVMStorage` object without verifying it against the actual atom or pair content. `Program.wrap()` propagates this cached value from any caller-supplied object. An attacker-controlled Python `CLVMStorage` object with a pre-set wrong `_cached_sha256_treehash` causes `Program.tree_hash()` to return a hash that does not correspond to the actual program content, producing a puzzle-hash mismatch in wallet-level operations.

---

### Finding Description

**Vulnerability class**: cache/allocator aliasing → hash/signature mismatch.

The state-inconsistency analog maps as follows:

| EigenLayer | clvm_rs |
|---|---|
| `sharesToUnderlyingView` reads `totalShares` (already decremented) while `_tokenBalance()` is not yet updated | `sha256_treehash` reads `_cached_sha256_treehash` (possibly stale or attacker-set) while the actual atom bytes are unchanged |
| Result: wrong share-to-underlying ratio | Result: wrong puzzle hash |

**Root cause — three cooperating sites:**

**1. `Treehasher.sha256_treehash()` — unconditional cache trust** [1](#0-0) 

```python
r = getattr(obj, "_cached_sha256_treehash", None)
if r is not None:
    self.cache_hits += 1
    hash_stack.append(r)
    return          # ← returns without any verification
```

If `_cached_sha256_treehash` is present on the object, the traversal stops immediately and the cached value is used as the tree hash. No check is made that the cached value is consistent with `obj.atom` or `obj.pair`.

**2. `Program.wrap()` — cache propagation from arbitrary objects** [2](#0-1) 

```python
o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
```

Any `CLVMStorage` object passed to `Program.wrap()` (and therefore to `Program.to()`) has its `_cached_sha256_treehash` copied verbatim into the resulting `Program` instance.

**3. `Program.tree_hash()` — returns cached value without recomputation** [3](#0-2) 

```python
def tree_hash(self) -> bytes:
    if self._cached_sha256_treehash is None:
        self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
    return self._cached_sha256_treehash
```

Once `_cached_sha256_treehash` is non-`None` (set by `wrap()`), `tree_hash()` returns it directly. The actual atom bytes are never consulted again.

**4. `CLVMTree.__init__()` — cache seeded from deserialized data** [4](#0-3) 

```python
if self.tree_hashes:
    self._cached_sha256_treehash = self.tree_hashes[index]
```

`CLVMTree` objects (produced by deserialization paths including `from_bytes_with_cursor`) set `_cached_sha256_treehash` directly from the `tree_hashes` list, which originates from the deserialized blob. If the blob is attacker-controlled, the cached hash is attacker-controlled.

---

### Impact Explanation

`Program.tree_hash()` is the canonical puzzle-hash computation entry point in the Python wallet layer. Puzzle hashes identify coins on the Chia blockchain: a coin is locked to a puzzle hash, and a spend is valid only if the puzzle hashes match. If `tree_hash()` returns a wrong value:

- A wallet computing a receive address derives the wrong puzzle hash → coins sent to that address are unrecognizable or unspendable.
- A wallet constructing a spend bundle uses the wrong puzzle hash → the spend is rejected by full nodes, or (in a crafted scenario) a spend for a different puzzle is accepted.
- Any downstream use of `curry_hash()` or `curry()` that calls `tree_hash()` internally inherits the wrong hash, silently corrupting curried puzzle hashes.

The Rust consensus engine validates spends independently and is not affected, but the Python wallet layer is the primary interface for coin creation and spend construction, making this a wallet-level hash/signature mismatch.

---

### Likelihood Explanation

The Python API explicitly accepts arbitrary `CLVMStorage` objects as input to `Program.to()` and `Program.wrap()`. The scope statement lists "Python objects" as a valid attacker-controlled entry path. Two realistic triggers exist:

1. **Direct Python object injection**: Any library or application that accepts a `CLVMStorage`-compatible object from an untrusted source (e.g., a plugin, a deserialized message, a third-party library) and passes it to `Program.to()` will propagate a wrong `_cached_sha256_treehash` without any warning.

2. **Crafted serde_2026 / `from_bytes_with_cursor` blob**: `CLVMTree.__init__` seeds `_cached_sha256_treehash` from `tree_hashes`, which comes from the deserialized blob. A crafted blob with wrong tree-hash entries causes every `CLVMTree` node to carry a wrong cached hash, which `Program.wrap()` then copies into the `Program` object.

---

### Recommendation

1. **Remove cache propagation from untrusted objects in `Program.wrap()`**: Do not copy `_cached_sha256_treehash` from an arbitrary `CLVMStorage` object. Only set it after computing the hash from first principles, or after verifying the cached value.

2. **Verify before trusting in `Treehasher.sha256_treehash()`**: For atoms, verify `_cached_sha256_treehash == sha256(b"\x01" + obj.atom)` before using the cached value. For pairs, the cached value can only be trusted if both children's hashes are also verified.

3. **Treat `_cached_sha256_treehash` as write-once from trusted code**: Make it a private attribute settable only by `Treehasher` itself, not readable from arbitrary external objects.

4. **Validate tree hashes during deserialization**: In `CLVMTree.from_bytes`, verify that each `tree_hashes[index]` matches the actual content of the node before storing it as `_cached_sha256_treehash`.

---

### Proof of Concept

```python
from clvm_rs.clvm_storage import CLVMStorage
from clvm_rs.program import Program
from clvm_rs.tree_hash import shatree_atom

class PoisonedStorage(CLVMStorage):
    """Atom containing b'foo' but with the hash of b'bar' pre-cached."""
    def __init__(self):
        self.atom = b"foo"
        self._pair = None
        # Attacker plants the hash of a *different* atom
        self._cached_sha256_treehash = shatree_atom(b"bar")

    @property
    def pair(self):
        return self._pair

# Program.to() → Program.wrap() copies _cached_sha256_treehash verbatim
p = Program.to(PoisonedStorage())

returned_hash  = p.tree_hash()          # reads cached value — wrong
correct_hash   = shatree_atom(b"foo")   # what the hash should be

assert returned_hash == shatree_atom(b"bar"), "cache was trusted"
assert returned_hash != correct_hash,         "hash mismatch confirmed"

# Any downstream puzzle-hash use is now silently wrong:
print("puzzle hash used by wallet:", returned_hash.hex())
print("correct puzzle hash:       ", correct_hash.hex())
```

The `Program.wrap()` call at [2](#0-1)  copies the attacker-planted hash, and `tree_hash()` at [5](#0-4)  returns it without ever consulting the actual atom bytes, exactly mirroring the EigenLayer pattern where `sharesToUnderlyingView` reads a partially-updated state variable and returns a wrong result.

### Citations

**File:** wheel/python/clvm_rs/tree_hash.py (L56-61)
```python
            obj = obj_stack.pop()
            r = getattr(obj, "_cached_sha256_treehash", None)
            if r is not None:
                self.cache_hits += 1
                hash_stack.append(r)
                return
```

**File:** wheel/python/clvm_rs/program.py (L141-143)
```python
        o._cached_serialization = getattr(v, "_cached_serialization", None)
        o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
        return o
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

**File:** wheel/python/clvm_rs/clvm_tree.py (L81-82)
```python
        if self.tree_hashes:
            self._cached_sha256_treehash = self.tree_hashes[index]
```
