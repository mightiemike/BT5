Looking at the vulnerability class — **dual-ledger accounting inconsistency** where one operation updates counter A but not counter B, which should always move together — I need to find the same pattern in clvm_rs.

The `Allocator` maintains two paired "ghost" counters for SmallAtom virtual allocations:
- `ghost_atoms`: counts virtual atom slots
- `ghost_heap`: counts virtual heap bytes those atoms would have consumed

Let me verify every SmallAtom creation path: