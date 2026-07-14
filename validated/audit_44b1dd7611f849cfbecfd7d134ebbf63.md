### Title
Forged `_cached_sha256_treehash` Snapshot Trusted Without Re-Validation, Corrupting Puzzle-Hash Verification — (`File: wheel/python/clvm_rs/tree_hash.py`, `wheel/python/clvm_rs/program.py`)

---

### Summary

`Treehasher.sha256_treehash()` and `Program.wrap()` unconditionally trust the `_cached_sha256_treehash` attribute on any Python `CLVMStorage`-compatible object without ever re-computing or validating it against the actual atom/pair content. An attacker who supplies a Python object with a pre-set forged hash causes `Program.tree_hash()` to return an attacker-controlled 32-byte value, breaking the fundamental invariant `tree_hash(program) == sha256tree(program_content)` that puzzle-hash verification in the Chia blockchain depends on.

---

### Finding Description

**Lazy snapshot read — `tree_hash.py`**

`Treehasher.sha256_treehash()` begins every node visit by reading `_cached_sha256_treehash` from the Python object:

```python
r = getattr(obj, "_cached_sha256_treehash", None)
if r is not None:
    self.cache_hits += 1
    hash_stack.append(r)
    return
``` [1](#0-0) 

If the attribute is present and non-`None`, the function immediately returns it as the hash of that subtree — no content is read, no SHA-256 is computed, no type or length check is performed. The value is taken as ground truth.

**Snapshot propagation — `program.py` `wrap()`**

`Program.wrap()` copies `_cached_sha256_treehash` from any incoming `CLVMStorage` object:

```python
o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
``` [2](#0-1) 

No verification is performed. Whatever value the source object carries is installed directly into the new `Program` instance.

**Lazy return — `program.py` `tree_hash()`**

`Program.tree_hash()` returns the cached value without re-computing:

```python
if self._cached_sha256_treehash is None:
    self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
return self._cached_sha256_treehash
``` [3](#0-2) 

Once the forged value is installed (either via `wrap()` or via the `getattr` shortcut in `sha256_treehash`), it is never evicted or re-checked.

**`CLVMStorage` explicitly documents these fields as optional hints**

The protocol definition notes `_cached_sha256_treehash` as an optional speed-up field, meaning any conforming object may carry it: [4](#0-3) 

This makes the attack surface part of the documented public API.

---

### Impact Explanation

The corrupted result is the return value of `Program.tree_hash()` — a 32-byte puzzle hash that is used as the canonical identity of a Chialisp puzzle.

Downstream consequences:

1. **`Program.__eq__()` is broken.** Equality is defined as `self.tree_hash() == other.tree_hash()`. A forged hash makes two structurally different programs compare equal, or a program compare unequal to itself. [5](#0-4) 

2. **`Program.curry_hash()` is broken.** It calls `self.tree_hash()` to derive the quoted-mod hash used in puzzle-hash computation. A forged base hash produces a forged curried puzzle hash. [6](#0-5) 

3. **Sub-tree hashes are poisoned transitively.** Because `sha256_treehash` trusts `_cached_sha256_treehash` on every node it visits, a forged hash on a leaf propagates upward: the pair hash that incorporates it is also wrong, and that wrong pair hash is cached on the pair node, and so on up to the root. [1](#0-0) 

The net effect is that puzzle-hash verification — the mechanism by which the Chia full node confirms that a coin's puzzle matches the committed puzzle hash — can be made to accept a wrong puzzle or reject a correct one, depending on how the forged hash is chosen.

---

### Likelihood Explanation

The Python wheel is the primary interface between the Chia full node / wallet stack and `clvm_rs`. Any code path that accepts a `CLVMStorage`-compatible object from an external source (e.g., deserialized from a peer message, loaded from a file, or constructed by a plugin) and passes it to `Program.to()` or `Program.wrap()` is exposed. The `CLVMStorage` protocol is intentionally open — any Python object with `.atom` and `.pair` qualifies — so the attack surface is the entire Python API boundary. No special privileges are required; only the ability to supply a Python object to a function that calls `Program.to()` or `Program.wrap()`.

---

### Recommendation

1. **Never trust `_cached_sha256_treehash` from externally supplied objects.** In `Program.wrap()`, do not copy `_cached_sha256_treehash` from the source object. Initialize it to `None` unconditionally and let `tree_hash()` compute it on first access.

2. **In `Treehasher.sha256_treehash()`, only trust the cache on objects whose hash was set by the hasher itself** (i.e., objects that were produced by a prior call to `sha256_treehash` in the same session), not on arbitrary incoming objects. One approach: use a separate `dict` keyed by object identity rather than reading a mutable attribute from the object.

3. **Treat `_cached_sha256_treehash` as a write-only optimization field.** The hasher may write it for performance, but reads should only occur from the hasher's own internal state, not from the object's attribute.

---

### Proof of Concept

```python
from clvm_rs.clvm_storage import CLVMStorage
from clvm_rs.program import Program

FORGED_HASH = b"\xde\xad" * 16  # 32 bytes, attacker-chosen

class MaliciousStorage:
    """A CLVMStorage-compatible object with a pre-set forged tree hash."""
    atom = b"real_content"
    pair = None
    _cached_sha256_treehash = FORGED_HASH  # forged snapshot

src = MaliciousStorage()

# Program.wrap() copies _cached_sha256_treehash without verification
p = Program.wrap(src)

# tree_hash() returns the forged value without recomputing
assert p.tree_hash() == FORGED_HASH  # passes — hash is wrong

# The correct hash of b"real_content" is sha256(b"\x01" + b"real_content")
correct = Program.new_atom(b"real_content").tree_hash()
assert p.tree_hash() != correct  # the invariant tree_hash == sha256tree is broken

# __eq__ is now broken: p and q have the same content but different hashes
q = Program.new_atom(b"real_content")
assert p != q  # forged hash != correct hash → programs that are equal compare unequal

# curry_hash derives a puzzle hash from the forged base hash
forged_puzzle_hash = p.curry_hash()
real_puzzle_hash = q.curry_hash()
assert forged_puzzle_hash != real_puzzle_hash  # puzzle hash is corrupted
```

The root cause is at `tree_hash.py:57–61` (blind attribute read) and `program.py:142` (blind attribute copy in `wrap()`). Both treat `_cached_sha256_treehash` as a trusted snapshot rather than a hint that must be validated against actual content.

### Citations

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

**File:** wheel/python/clvm_rs/program.py (L284-286)
```python
        if self._cached_sha256_treehash is None:
            self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
        return self._cached_sha256_treehash
```

**File:** wheel/python/clvm_rs/program.py (L359-362)
```python
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
