### Title
Unconditionally Trusted Mutable `_cached_sha256_treehash` Attribute Allows Puzzle-Hash Forgery — (`File: wheel/python/clvm_rs/tree_hash.py`, `wheel/python/clvm_rs/program.py`)

---

### Summary

`Treehasher.sha256_treehash()` and `Program.tree_hash()` unconditionally trust the `_cached_sha256_treehash` attribute read from any caller-supplied `CLVMStorage` object, without verifying it against the actual tree content. An attacker who supplies a `CLVMStorage` object with a pre-set, forged `_cached_sha256_treehash` causes the entire hash derivation to short-circuit and return the wrong value. This is the direct analog of the `balanceOf`-vs-`getReserves` class: a mutable, externally-settable attribute is read as if it were canonical state.

---

### Finding Description

`CLVMStorage` is a structural Python `Protocol` — any object with `atom` and `pair` attributes satisfies it. The `_cached_sha256_treehash` field is documented only as an *optional speed hint*:

```
# `_cached_sha256_treehash: Optional[bytes]` is used by `sha256_treehash`
# to speed up serialization
``` [1](#0-0) 

`Treehasher.sha256_treehash()` reads this attribute with `getattr` and, if it is non-`None`, immediately appends it to the hash stack and returns — **no validation against the actual atom/pair content is performed**:

```python
r = getattr(obj, "_cached_sha256_treehash", None)
if r is not None:
    self.cache_hits += 1
    hash_stack.append(r)
    return
``` [2](#0-1) 

`Program.wrap()` propagates the attribute from any incoming `CLVMStorage` object directly into the new `Program` instance:

```python
o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
``` [3](#0-2) 

`Program.tree_hash()` then returns the cached value without recomputing:

```python
def tree_hash(self) -> bytes:
    if self._cached_sha256_treehash is None:
        self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
    return self._cached_sha256_treehash
``` [4](#0-3) 

The same pattern exists for `_cached_serialization` in `sexp_to_byte_iterator()`:

```python
r = getattr(sexp, "_cached_serialization", None)
if r is not None:
    yield r
    continue
``` [5](#0-4) 

---

### Impact Explanation

In Chia, the SHA-256 tree hash of a puzzle program **is** the coin's puzzle hash — the on-chain identifier used to lock and unlock coins. Any code path that calls `Program.tree_hash()` or `sha256_treehash()` on an attacker-supplied `CLVMStorage` object will return the forged hash instead of the real one. Downstream consequences include:

- **`Program.__eq__`** uses `tree_hash()` for equality:
  ```python
  return self.tree_hash() == other_obj.tree_hash()
  ``` [6](#0-5) 
  Two programs with different content but the same forged hash compare as equal.

- **`Program.curry_hash()`** feeds `self.tree_hash()` into `curry_and_treehash`, producing a wrong curried puzzle hash. [7](#0-6) 

- **`sexp_to_byte_iterator`** trusts `_cached_serialization` the same way, so a forged serialization cache causes the wrong bytes to be sent to the Rust interpreter, making the executed program differ from the one whose hash was computed. [5](#0-4) 

---

### Likelihood Explanation

The `CLVMStorage` protocol is structural — no explicit registration or subclassing is required. Any Python object with `atom` and `pair` attributes qualifies. Wallet software, puzzle drivers, and coin-spend builders routinely accept `CLVMStorage`-compatible objects from external sources (deserialized network data, third-party libraries, plugin code). Setting `_cached_sha256_treehash` on a plain Python object is a single attribute assignment. The attack requires no memory corruption, no Rust interaction, and no privileged access — only the ability to pass a crafted Python object to any code that calls `sha256_treehash()` or `Program.wrap()`.

---

### Recommendation

Remove the unconditional trust in `_cached_sha256_treehash`. Either:

1. **Validate on read**: When the cached value is present, verify it matches the computed hash before using it (at least in non-performance-critical paths such as `Program.wrap()`).
2. **Restrict the cache to internally-set values only**: Only set and read `_cached_sha256_treehash` on objects whose content is controlled by the library (e.g., only on `Program` instances created by `Program.new_atom`, `Program.new_pair`, or deserialization), and never read it from arbitrary caller-supplied `CLVMStorage` objects.
3. **Apply the same fix to `_cached_serialization`** in `sexp_to_byte_iterator`.

---

### Proof of Concept

```python
from clvm_rs.clvm_storage import CLVMStorage
from clvm_rs.tree_hash import sha256_treehash
from clvm_rs.program import Program

# Craft a CLVMStorage atom whose content is b"\x01" (hash = sha256(b"\x01" + b"\x01"))
# but whose _cached_sha256_treehash is forged to all-zeros.
class ForgedStorage:
    atom = b"\x01"
    _pair = None
    _cached_sha256_treehash = b"\x00" * 32  # forged hash

    @property
    def pair(self):
        return self._pair

forged = ForgedStorage()

# sha256_treehash trusts the cached attribute unconditionally
result = sha256_treehash(forged)
assert result == b"\x00" * 32  # returns forged hash, not the real one

# Program.wrap() propagates the forged hash
p = Program.wrap(forged)
assert p.tree_hash() == b"\x00" * 32  # puzzle hash is wrong

# Program.__eq__ now considers this equal to any program whose hash is all-zeros
p2 = Program.wrap(ForgedStorage())
assert p == p2  # True even if content differs
```

### Citations

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

**File:** wheel/python/clvm_rs/program.py (L142-142)
```python
        o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
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

**File:** wheel/python/clvm_rs/ser.py (L34-37)
```python
        r = getattr(sexp, "_cached_serialization", None)
        if r is not None:
            yield r
            continue
```
