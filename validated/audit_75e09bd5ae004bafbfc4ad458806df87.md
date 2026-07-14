### Title
`Serializer::add()` Panics Instead of Returning `Err` on Post-Completion Call — (`File: src/serde/incremental.rs`)

### Summary
`Serializer::add()` is a public API function whose return type is `Result<(bool, UndoState)>`, signalling to callers that all error conditions are communicated via `Err(...)`. However, when called after serialization is already complete (i.e., after it has returned `Ok((true, ...))`), the function unconditionally panics via `assert!` instead of returning `Err`. This breaks the caller's error-handling contract in exactly the same way the original report describes: a function expected to return a graceful error value instead aborts execution unexpectedly.

### Finding Description
`Serializer::add()` in `src/serde/incremental.rs` contains two `assert!` calls that fire panics inside a `Result`-returning function:

```rust
pub fn add(&mut self, a: &Allocator, node: NodePtr) -> Result<(bool, UndoState)> {
    // once we're done serializing (i.e. there was no sentinel in the last
    // call to add()), we can't resume
    assert!(!self.read_op_stack.is_empty());   // ← panics, not Err
    ...
    let op = self.read_op_stack.pop();
    assert!(op == Some(ReadOp::Parse));         // ← panics, not Err
```

The first `assert!` fires when `add()` is called after the serializer has already finished (i.e., `read_op_stack` is empty). The comment explicitly acknowledges this is an error condition ("we can't resume"), yet the function panics rather than returning `Err(...)`. The second `assert!` fires when the internal `read_op_stack` contains an unexpected `ReadOp::Cons` entry, which is also an error condition that should propagate as `Err`.

Both conditions are reachable through repeated API use: any caller that invokes `add()` a second time after receiving `Ok((true, ...))` — or that drives the serializer with attacker-influenced node sequences that corrupt the internal stack ordering — will receive a process-aborting panic rather than a catchable `Err`.

### Impact Explanation
Callers of `Serializer::add()` use `?` or `match` to handle `Err` returns. A panic bypasses all of that error-handling code entirely, unwinding (or aborting, if `panic = "abort"`) the calling thread. In a consensus-critical node process, this means a crash rather than a graceful rejection. In a PyO3 context, a Rust panic inside a Python-exposed call is undefined behavior and typically terminates the Python process. The broken invariant is: **the function's return type promises `Result` but delivers a panic**, which is the direct analog of the original report's "function expected to return 0 but reverts instead."

### Likelihood Explanation
The `Serializer` is a public API (`pub struct Serializer`, `pub fn add`). Any downstream Rust consumer of `clvmr` that uses incremental serialization and calls `add()` after completion — which is a natural mistake given that the only enforcement is a comment, not a type-level guarantee — will trigger the panic. The criteria explicitly include "repeated serialization/cache/API use" as a valid attacker-controlled entry path. A caller driven by attacker-supplied CLVM node sequences (e.g., a node that serializes received programs for storage) can trigger the second assert by producing a tree structure that causes the internal `read_op_stack` to be in an unexpected state.

### Recommendation
Replace both `assert!` calls with proper `Result`-returning error paths:

```rust
if self.read_op_stack.is_empty() {
    return Err(EvalErr::InternalError(
        NodePtr::NIL,
        "Serializer::add called after serialization is complete".to_string(),
    ));
}
...
if op != Some(ReadOp::Parse) {
    return Err(EvalErr::InternalError(
        NodePtr::NIL,
        "unexpected op on read_op_stack".to_string(),
    ));
}
```

This ensures callers receive a catchable `Err` instead of a process-terminating panic, consistent with the function's declared return type.

### Proof of Concept

```rust
use clvmr::allocator::Allocator;
use clvmr::serde::Serializer;

fn main() {
    let mut a = Allocator::new();
    let atom = a.new_atom(&[1, 2, 3]).unwrap();

    // No sentinel: serializer will complete on first add()
    let mut ser = Serializer::new(None);

    // First call: returns Ok((true, ...)) — serialization complete
    let (done, _undo) = ser.add(&a, atom).unwrap();
    assert!(done);

    // Second call: caller expects Ok or Err, but gets a PANIC
    // assert!(!self.read_op_stack.is_empty()) fires
    let _ = ser.add(&a, atom); // process aborts here
}
```

The root cause is at: [1](#0-0) 

and the second assert: [2](#0-1)

### Citations

**File:** src/serde/incremental.rs (L51-54)
```rust
    pub fn add(&mut self, a: &Allocator, node: NodePtr) -> Result<(bool, UndoState)> {
        // once we're done serializing (i.e. there was no sentinel in the last
        // call to add()), we can't resume
        assert!(!self.read_op_stack.is_empty());
```

**File:** src/serde/incremental.rs (L72-74)
```rust
            let op = self.read_op_stack.pop();
            assert!(op == Some(ReadOp::Parse));

```
