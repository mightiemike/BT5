### Title
Unverified `_cached_sha256_treehash` Propagation in `Program.wrap()` Enables Puzzle-Hash Spoofing — (File: `wheel/python/clvm_rs/program.py`)

---

### Summary

`Program.wrap()` blindly copies `_cached_sha256_treehash` from any caller-supplied `CLVMStorage` object without verifying it against the actual tree content. `Program.tree_hash()` and `sha256_treehash()` then return this unverified value as authoritative. Any Python object implementing the `CLVMStorage` protocol can inject an arbitrary 32-byte hash that will be used as the puzzle hash for that program, breaking equality checks, `curry_hash()` derivations, and any downstream coin-locking logic that depends on `tree_hash()`.

---

### Finding Description

The `CLVMStorage` protocol documents `_cached_sha256_treehash` as an optional optimization hint. However, `Program.wrap()` unconditionally propagates it:

```python
# wheel/python/clvm_rs/program.py, line 142
o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
```

`Program.tree_hash()` then short-circuits on this value without recomputing:

```python
# wheel/python/clvm_rs/program.py, lines 284-286
if self._cached_sha256_treehash is None:
    self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
return self._cached_sha256_treehash
```

`sha256_treehash()` (the `Treehasher.sha256_treehash` method) also short-circuits on the same attribute on any sub-node:

```python
# wheel/python/clvm_rs/tree_hash.py, lines 57-61
r = getattr(obj, "_cached_sha256_treehash", None)
if r is not None:
    self.cache_hits += 1
    hash_stack.append(r)
    return
```

No step verifies that the cached value equals `sha256tree(atom)` for atoms or `sha256tree(left, right)` for pairs. The hash is accepted as-is from the caller-supplied object.

---

### Impact Explanation

The corrupted result is the return value of `Program.tree_hash()`, which is used as the **puzzle hash** in the Chia blockchain. Concrete downstream effects:

1. **`Program.__eq__`** compares programs by `tree_hash()`. A spoofed hash makes two programs with different content compare as equal, or a program compare as unequal to itself. [1](#0-0) 

2. **`Program.curry_hash()`** derives curried puzzle hashes from `self.tree_hash()`. A spoofed hash produces a wrong puzzle hash, causing coins to be locked to a puzzle that does not match the on-chain spend. [2](#0-1) 

3. **Any wallet or node code** that calls `program.tree_hash()` to derive a coin's puzzle hash receives a wrong value, breaking coin-ID computation and spend-bundle validation.

---

### Likelihood Explanation

The `CLVMStorage` protocol is a public Python interface. Any Python code that creates an object with `atom`, `pair`, and `_cached_sha256_treehash` attributes and passes it to `Program.to()` or `Program.wrap()` triggers this path. The `CLVMStorage` protocol is explicitly documented as accepting `_cached_sha256_treehash` as an optional field, making it a natural extension point for third-party code. No special privileges or internal access are required. [3](#0-2) 

---

### Recommendation

Before returning `_cached_sha256_treehash` from `Program.tree_hash()`, verify it against the computed hash, or do not propagate the field from external `CLVMStorage` objects in `Program.wrap()`. At minimum, `Program.wrap()` should not copy `_cached_sha256_treehash` from objects that are not already `Program` instances (i.e., objects whose hash provenance is unknown). Alternatively, treat `_cached_sha256_treehash` as a write-once field that is only set by the hasher itself, never read from external objects.

---

### Proof of Concept

```python
from clvm_rs import Program
from clvm_rs.clvm_storage import CLVMStorage

class MaliciousStorage:
    """CLVMStorage-compatible object with a fake tree hash."""
    atom = b"real_content"
    pair = None
    _cached_sha256_treehash = b"\xde\xad" * 16  # 32 bytes of garbage

malicious = MaliciousStorage()
p = Program.wrap(malicious)

# tree_hash() returns the fake hash, not sha256tree(b"real_content")
print(p.tree_hash().hex())
# => "deaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddead"

# Correct hash for b"real_content":
correct = Program.new_atom(b"real_content")
print(correct.tree_hash().hex())
# => (actual sha256tree value, different from above)

# Equality check is broken:
print(p == correct)  # False (hashes differ), but p and correct have same atom
# Or: inject the correct hash into a different atom to make them compare equal
```

The `Program.wrap()` call at line 142 copies the fake hash unconditionally. [4](#0-3) 

The `tree_hash()` method returns it without recomputation at line 284. [5](#0-4) 

The `sha256_treehash` function also short-circuits on it for any sub-node at line 57. [6](#0-5)

### Citations

**File:** wheel/python/clvm_rs/program.py (L132-143)
```python
    @classmethod
    def wrap(cls, v: CLVMStorage) -> Program:
        if isinstance(v, Program):
            return v
        o = cls()
        o.atom = v.atom
        o._pair = None
        o._unwrapped = v
        o._unwrapped_pair = v.pair
        o._cached_serialization = getattr(v, "_cached_serialization", None)
        o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
        return o
```

**File:** wheel/python/clvm_rs/program.py (L182-188)
```python
    def __eq__(self, other) -> bool:
        try:
            other_obj = self.to(other)
        except ValueError:
            # cast failure
            return False
        return self.tree_hash() == other_obj.tree_hash()
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

**File:** wheel/python/clvm_rs/program.py (L341-362)
```python
    def curry_hash(self, *args: bytes) -> bytes:
        """
        Return a puzzle hash that would be created if you curried this puzzle
        with arguments that have the given hashes.

        In other words,

        ```
        c1 = self.curry(arg1, arg2, arg3).tree_hash()
        c2 = self.curry_hash(arg1.tree_hash(), arg2.tree_hash(), arg3.tree_hash())
        assert c1 == c2  # they will be the same
        ```

        This looks useless to the unitiated, but sometimes you'll need a puzzle
        hash where you don't actually know the contents of a clvm subtree -- just its
        hash. This lets you calculate the puzzle hash with hidden information.
        """
        curry_treehasher = self.curry_treehasher
        quoted_mod_hash = curry_treehasher.calculate_hash_of_quoted_mod_hash(
            self.tree_hash()
        )
        return curry_treehasher.curry_and_treehash(quoted_mod_hash, *args)
```

**File:** wheel/python/clvm_rs/clvm_storage.py (L18-22)
```python
    # optional fields used to speed implementations:

    # `_cached_sha256_treehash: Optional[bytes]` is used by `sha256_treehash`
    # `_cached_serialization:  bytes` is used by `sexp_to_byte_iterator`
    #      to speed up serialization
```

**File:** wheel/python/clvm_rs/tree_hash.py (L57-61)
```python
            r = getattr(obj, "_cached_sha256_treehash", None)
            if r is not None:
                self.cache_hits += 1
                hash_stack.append(r)
                return
```
