### Title
`Treehasher.sha256_treehash` Ignores Instance Prefix Configuration and Cross-Contaminates `_cached_sha256_treehash` Across Hash Domains - (File: `wheel/python/clvm_rs/tree_hash.py`)

---

### Summary

`Treehasher` is designed to support configurable hash domains via `atom_prefix` and `pair_prefix`. However, `sha256_treehash` silently ignores the instance's configured prefixes and always calls the module-level `shatree_atom`/`shatree_pair` functions, which are permanently bound to the Chia-standard `CHIA_TREEHASHER` instance. Additionally, the `_cached_sha256_treehash` attribute written onto `CLVMStorage` objects carries no prefix-context tag, so a hash computed under one domain is silently returned as authoritative by any other `Treehasher` instance operating under a different domain.

---

### Finding Description

`Treehasher.__init__` accepts `atom_prefix` and `pair_prefix` and stores them as instance attributes. The instance methods `shatree_atom` and `shatree_pair` correctly use `self.atom_prefix` / `self.pair_prefix`: [1](#0-0) 

However, `sha256_treehash` — the primary traversal method — calls the **module-level** `shatree_atom` (line 63) and `shatree_pair` (line 86) instead of `self.shatree_atom` / `self.shatree_pair`: [2](#0-1) [3](#0-2) 

Those module-level names are bound at import time to `CHIA_TREEHASHER`, the singleton with the Chia-standard `0x01`/`0x02` prefixes: [4](#0-3) 

The cache written to `obj._cached_sha256_treehash` carries no prefix tag. Any subsequent call to `sha256_treehash` on any `Treehasher` instance — regardless of its configured prefixes — reads this attribute and returns it immediately as a cache hit: [5](#0-4) 

This is the exact structural analog to the reported vulnerability: a value computed in one context (one prefix domain) is accepted without re-verification in a different context (a different prefix domain), because the context identifier is absent from the cached artifact.

---

### Impact Explanation

**Corrupted result:** Any `Treehasher` instance constructed with non-standard prefixes will return the Chia-standard SHA-256 tree hash from `sha256_treehash`, not the hash under its own domain. The returned `bytes` value is silently wrong — no exception is raised.

**Cache cross-contamination:** Once a `CLVMStorage` node is hashed by `CHIA_TREEHASHER` (or any call path that reaches the module-level functions), `_cached_sha256_treehash` is set on the object. A subsequent call through a custom-prefix `Treehasher` reads that attribute and returns the Chia-domain hash as if it were the custom-domain hash. The reverse is also true: if a custom-prefix hasher somehow populates the cache first (e.g., via a direct `setattr`), the Chia-standard hasher will return the wrong value.

In the Chia ecosystem, tree hashes are used as puzzle hashes — the identifiers that lock coins. A wrong puzzle hash means a coin is locked to an address that does not correspond to the intended puzzle, or a puzzle-hash comparison in a spend bundle passes when it should not (or fails when it should pass), depending on which direction the contamination flows.

---

### Likelihood Explanation

`Treehasher` is a public, documented class in the Python wheel with a constructor that explicitly accepts custom prefixes, signalling that multi-domain use is an intended and supported pattern. `CurryTreehasher` in `curry_and_treehash.py` already imports the module-level `shatree_atom`/`shatree_pair` directly, propagating the same wrong binding into curry-hash computations for any dialect whose keywords differ from the Chia standard: [6](#0-5) 

Any downstream library or tool that instantiates `Treehasher` with non-standard prefixes — for example, to compute puzzle hashes under a test network, a forked chain, or a future softfork domain — will silently receive Chia-mainnet hashes. The bug is invisible at the call site because the return type and shape are identical.

---

### Recommendation

1. Replace the bare `shatree_atom` / `shatree_pair` calls inside `sha256_treehash` with `self.shatree_atom` / `self.shatree_pair` so the instance's configured prefixes are actually used.
2. Key the `_cached_sha256_treehash` attribute by `(atom_prefix, pair_prefix)` — for example, store a dict `_treehash_cache: Dict[Tuple[bytes,bytes], bytes]` on the object — so that hashes computed under different prefix domains do not alias each other.

---

### Proof of Concept

```python
from wheel.python.clvm_rs.tree_hash import Treehasher, CHIA_TREE_HASH_ATOM_PREFIX, CHIA_TREE_HASH_PAIR_PREFIX
from wheel.python.clvm_rs.clvm_storage import CLVMStorage  # or any CLVMStorage-compatible object

# Standard Chia hasher
chia_hasher = Treehasher(CHIA_TREE_HASH_ATOM_PREFIX, CHIA_TREE_HASH_PAIR_PREFIX)

# Custom-domain hasher with different prefixes
custom_hasher = Treehasher(b'\x03', b'\x04')

class SimpleAtom:
    atom = b"hello"
    pair = None

obj = SimpleAtom()

h_chia   = chia_hasher.sha256_treehash(obj)
h_custom = custom_hasher.sha256_treehash(obj)

# BUG: h_custom == h_chia, not sha256(b'\x03' + b'hello')
assert h_chia == h_custom, "cross-domain hash aliasing confirmed"

# Cache contamination: obj._cached_sha256_treehash is now set to the Chia hash.
# Any future call through custom_hasher returns the Chia hash without recomputing.
obj2 = SimpleAtom()
_ = chia_hasher.sha256_treehash(obj2)          # populates cache with Chia hash
h_custom2 = custom_hasher.sha256_treehash(obj2) # reads Chia hash from cache
assert h_custom2 == h_chia                      # wrong domain hash returned silently
```

The root cause is at lines 63 and 86 of `wheel/python/clvm_rs/tree_hash.py`, where `shatree_atom` and `shatree_pair` resolve to the module-level closures over `CHIA_TREEHASHER` rather than to `self.shatree_atom` / `self.shatree_pair`. [7](#0-6)

### Citations

**File:** wheel/python/clvm_rs/tree_hash.py (L32-48)
```python
    def __init__(self, atom_prefix: bytes, pair_prefix: bytes):
        self.atom_prefix = atom_prefix
        self.pair_prefix = pair_prefix
        self.cache_hits = 0

    def shatree_atom(self, atom: bytes) -> bytes:
        s = sha256()
        s.update(self.atom_prefix)
        s.update(atom)
        return s.digest()

    def shatree_pair(self, left_hash: bytes, right_hash: bytes) -> bytes:
        s = sha256()
        s.update(self.pair_prefix)
        s.update(left_hash)
        s.update(right_hash)
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

**File:** wheel/python/clvm_rs/curry_and_treehash.py (L7-7)
```python
from .tree_hash import shatree_pair, shatree_atom
```
