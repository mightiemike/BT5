Looking at the external report's vulnerability class — **error swallowing that bypasses subsequent sequential state operations, leading to corrupted/incomplete state** — I need to find an analog in chain-main where a specific error is caught and silenced, causing later critical operations to be skipped while state is partially mutated.

Let me examine the tiered rewards slash path more closely.