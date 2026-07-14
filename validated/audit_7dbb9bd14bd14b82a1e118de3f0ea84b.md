### Title
Stale `_cached_sha256_treehash` Trusted Without Verification Yields Wrong Puzzle Hash — (File: `wheel/python/clvm_rs/tree_hash.py`)

### Summary
`sha256_treehash()` and `Program.tree_hash()` blindly trust the `_cached_sha256_treehash` attribute on any caller-supplied Python object without verifying it matches the actual node content. An attacker who can pass a `CLVMStorage`-shaped Python object with a pre-set `_cached_sha256_treehash` causes the wrong 32-byte hash to be returned, corrupting puzzle-hash computation and equality checks throughout the Python API layer.

### Finding Description
The vulnerability class from the external report is