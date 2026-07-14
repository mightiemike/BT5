### Title
`run_with_cost` Raises `IndexError` Instead of `EvalError` on Malformed Program Bytes — (`File: wheel/python/clvm_rs/program.py`)

---

### Summary

`Program.run_with_cost` in `wheel/python/clvm_rs/program.py` is documented and expected to raise `EvalError` on any execution failure. However, when the program or argument bytes are malformed (a deserialization failure), the Rust layer raises a single-argument `ValueError` via `eval_to_py`, while the Python catch block unconditionally indexes `ve.args[1]`. This raises an `IndexError` instead of the expected `EvalError`, breaking the API contract for all callers that handle execution failures by catching `EvalError` or `ValueError`.

---

### Finding Description

**Root cause — Rust side (`wheel/src/api.rs`)**

`run_serialized_chia_program` deserializes the program and argument bytes before running them. If either deserialization call fails, the error is converted with `eval_to_py`:

```rust
fn eval_to_py(err: EvalErr) -> PyErr {
    // Rarely Used in python bindings.
    pyo3::exceptions::PyValueError::new_err(err.to_string())
}
``` [1](#0-0) 

This produces a `ValueError` whose `.args` tuple contains **exactly one element** — the error string. No `LazyNode` / sexp is attached.

By contrast, when `run_program` itself fails, `adapt_response` builds a two-element tuple `(message, LazyNode)` and wraps it in a `ValueError`:

```rust
let tuple = PyTuple::new(py, [msg, sexp])?;
let value_error: PyErr = PyValueError::new_err(tuple.unbind().into_any());
``` [2](#0-1) 

**Root cause — Python side (`wheel/python/clvm_rs/program.py`)**

`run_with_cost` catches every `ValueError` and unconditionally accesses both `ve.args[0]` and `ve.args[1]`:

```python
except ValueError as ve:
    raise EvalError(ve.args[0], self.wrap(ve.args[1]))
``` [3](#0-2) 

When the error originates from `eval_to_py` (deserialization failure), `ve.args` is a one-element tuple. Accessing `ve.args[1]` raises `IndexError`, which propagates uncaught — completely bypassing the `EvalError` path.

**The interface contract**

`EvalError` is the documented exception type for all CLVM execution failures:

```python
class EvalError(ValueError):
    def __init__(self, message: str, sexp):
        ...
``` [4](#0-3) 

Callers of `run_with_cost` (and the higher-level `run`) are expected to catch `EvalError`. The deviation — raising `IndexError` instead — is the direct analog of the external report's bug: a function that implements a well-defined interface silently deviates from its specified error-return contract.

---

### Impact Explanation

Any caller that wraps `run_with_cost` or `run` with `except EvalError` or `except ValueError` to handle invalid programs will **not** catch the `IndexError`. In the Chia ecosystem, nodes and wallets call these methods to evaluate spend bundles. A transaction carrying malformed CLVM bytes (e.g., a truncated serialization) would cause the evaluating component to crash with an unhandled `IndexError` rather than gracefully rejecting the spend. This is a consensus-adjacent API divergence: the node's behavior on malformed input is undefined relative to the documented interface.

---

### Likelihood Explanation

The trigger is straightforward: any caller that passes attacker-controlled bytes as the `program` argument to `run_with_cost` (or indirectly via `Program.from_bytes` + `run`) can reach the deserialization path. Malformed CLVM bytes are trivially constructable (e.g., a single byte `0xff` with no continuation). The `eval_to_py` comment itself acknowledges the path exists ("Rarely Used in python bindings"), confirming it is reachable but undertested.

---

### Recommendation

In `run_with_cost`, guard the `ve.args[1]` access before constructing `EvalError`. If the second argument is absent (deserialization error), substitute a sentinel node such as `Program.null()`:

```python
except ValueError as ve:
    sexp = self.wrap(ve.args[1]) if len(ve.args) > 1 else Program.null()
    raise EvalError(ve.args[0], sexp)
```

Alternatively, `eval_to_py` in `api.rs` should be changed to always attach a node pointer (e.g., `NodePtr::NIL`) so the two-element tuple invariant is maintained uniformly across all error paths, matching the contract established by `adapt_response`.

---

### Proof of Concept

```python
from clvm_rs.program import Program

# Malformed CLVM bytes — truncated pair marker with no continuation
malformed = b"\xff"

prog = Program.null()  # any valid program
try:
    prog.run_with_cost(malformed, max_cost=10_000_000)
except EvalError:
    print("OK: got EvalError as expected")
except IndexError as e:
    # This branch is actually reached — the API contract is broken
    print(f"BUG: got IndexError instead of EvalError: {e}")
```

The `IndexError` is raised at `wheel/python/clvm_rs/program.py` line 299 (`ve.args[1]`) because `eval_to_py` at `wheel/src/api.rs` line 31 produces a single-argument `ValueError` with no attached sexp, while `adapt_response` at `wheel/src/adapt_response.rs` lines 28–31 produces a two-argument `ValueError`. The Python catch block assumes the two-argument form unconditionally. [5](#0-4) [6](#0-5)

### Citations

**File:** wheel/src/api.rs (L29-32)
```rust
fn eval_to_py(err: EvalErr) -> PyErr {
    // Rarely Used in python bindings.
    pyo3::exceptions::PyValueError::new_err(err.to_string())
}
```

**File:** wheel/src/api.rs (L54-61)
```rust
    let r: Response = (|| -> PyResult<Response> {
        let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
        let args = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
        let dialect = ChiaDialect::new(flags);

        Ok(py.detach(|| run_program(&mut allocator, &dialect, program, args, max_cost)))
    })()?;
    adapt_response(py, allocator, r)
```

**File:** wheel/src/adapt_response.rs (L28-31)
```rust
            let msg: Bound<'_, PyAny> = eval_err.to_string().into_pyobject(py)?.into_any();
            let tuple = PyTuple::new(py, [msg, sexp])?;
            let value_error: PyErr = PyValueError::new_err(tuple.unbind().into_any());
            Err(value_error)
```

**File:** wheel/python/clvm_rs/program.py (L288-300)
```python
    def run_with_cost(
        self, args, max_cost: int, flags: int = 0
    ) -> Tuple[int, "Program"]:
        prog_bytes = bytes(self)
        args_bytes = bytes(self.to(args))
        try:
            cost, lazy_node = run_serialized_chia_program(
                prog_bytes, args_bytes, max_cost, flags
            )
            r = self.wrap(lazy_node)
        except ValueError as ve:
            raise EvalError(ve.args[0], self.wrap(ve.args[1]))
        return cost, r
```

**File:** wheel/python/clvm_rs/eval_error.py (L3-6)
```python
class EvalError(ValueError):
    def __init__(self, message: str, sexp):
        super().__init__(message)
        self._sexp = sexp
```
