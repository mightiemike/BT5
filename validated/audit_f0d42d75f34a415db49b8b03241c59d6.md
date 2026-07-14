### Title
Python `run_with_cost` Cannot Handle Deserialization `ValueError` from Rust — Raises `IndexError` Instead of `EvalError` - (File: `wheel/python/clvm_rs/program.py` / `wheel/src/api.rs`)

---

### Summary

The Python `Program.run_with_cost()` method catches `ValueError` from the Rust layer and unconditionally accesses `ve.args[1]` (the offending node). However, when the Rust `run_serialized_chia_program` fails during **deserialization** (before execution), it uses `eval_to_py()` which creates a `ValueError` with only **one** argument (a string). Accessing `ve.args[1]` on a one-element tuple raises `IndexError: tuple index out of range` instead of the expected `EvalError`. Any caller that guards against `EvalError` will not catch this `IndexError`, causing unexpected crashes.

---

### Finding Description

In `wheel/src/api.rs`, the helper `eval_to_py` converts a Rust `EvalErr` into a Python `ValueError` carrying only a single string argument:

```rust
fn eval_to_py(err: EvalErr) -> PyErr {
    // Rarely Used in python bindings.
    pyo3::exceptions::PyValueError::new_err(err.to_string())
}
``` [1](#0-0) 

This `eval_to_py` is used as the error mapper for both `node_from_bytes` calls inside `run_serialized_chia_program`:

```rust
let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
let args = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
``` [2](#0-1) 

If either deserialization call fails (e.g., attacker-supplied malformed CLVM bytes), the resulting `PyErr` is a `ValueError` whose `.args` tuple contains **only one element** — the error string. The `adapt_response` path is never reached.

On the Python side, `Program.run_with_cost()` catches `ValueError` and unconditionally indexes `ve.args[1]`:

```python
except ValueError as ve:
    raise EvalError(ve.args[0], self.wrap(ve.args[1]))
``` [3](#0-2) 

When `ve.args` is `("some error string",)` — a one-element tuple — `ve.args[1]` raises `IndexError: tuple index out of range`. The Python caller **cannot receive** the error format that the Rust deserialization layer sends back, exactly mirroring the SpokeVault pattern: the caller lacks the capability to handle what the callee returns.

---

### Impact Explanation

Any Python code that calls `Program.run_with_cost()` (or `Program.run()`) with attacker-controlled, malformed CLVM bytes will receive an `IndexError` instead of an `EvalError`. Code that defensively catches `EvalError` to reject invalid programs — such as wallet validators, mempool filters, or coin-spend verifiers — will **not** catch `IndexError`, causing an unhandled exception and a crash. In a consensus-validation context this can cause a node to abort processing a block or spend bundle, constituting a remotely-triggerable denial-of-service via crafted CLVM bytes.

---

### Likelihood Explanation

CLVM program bytes are routinely received from untrusted sources: peer nodes, user-submitted spend bundles, and wallet RPC inputs. Crafting bytes that fail `node_from_bytes` (e.g., a truncated or structurally invalid serialization) is trivial. The trigger requires no special privileges and is reachable through the standard public Python API.

---

### Recommendation

Replace the bare `ve.args[1]` access with a safe fallback. When the `ValueError` originates from a deserialization failure it carries no node argument, so the handler should guard against a one-element `args` tuple:

```python
except ValueError as ve:
    node_arg = ve.args[1] if len(ve.args) > 1 else self.allocator.nil()
    raise EvalError(ve.args[0], self.wrap(node_arg))
```

Alternatively, change `eval_to_py` to always include a sentinel node (e.g., `NodePtr::NIL` serialized) as the second argument so the Python handler's assumption is always satisfied. The comment in `eval_to_py` ("Rarely Used in python bindings") acknowledges the divergence but does not fix it.

---

### Proof of Concept

```python
from clvm_rs.program import Program

# Malformed CLVM bytes — truncated atom header causes node_from_bytes to fail
malformed = b"\xff"  # pair marker with no children

try:
    prog = Program.from_bytes(b"\x01")  # valid program: (quote . 1)
    prog.run_with_cost(Program.from_bytes(malformed), max_cost=10_000_000)
except EvalError:
    print("caught EvalError as expected")
except IndexError as e:
    # This branch is hit instead:
    print(f"BUG: got IndexError: {e}")
    # IndexError: tuple index out of range
```

The `run_with_cost` call passes `malformed` as the `args` bytes. Inside `run_serialized_chia_program`, `node_from_bytes(&mut allocator, args)` fails; `eval_to_py` wraps the error as a one-arg `ValueError`; Python's `except ValueError` handler then crashes on `ve.args[1]`, surfacing `IndexError` to the caller instead of `EvalError`. [4](#0-3) [5](#0-4)

### Citations

**File:** wheel/src/api.rs (L29-32)
```rust
fn eval_to_py(err: EvalErr) -> PyErr {
    // Rarely Used in python bindings.
    pyo3::exceptions::PyValueError::new_err(err.to_string())
}
```

**File:** wheel/src/api.rs (L40-62)
```rust
pub fn run_serialized_chia_program(
    py: Python,
    program: &[u8],
    args: &[u8],
    max_cost: Cost,
    flags: u32,
) -> PyResult<(u64, LazyNode)> {
    let flags = ClvmFlags::from_bits_truncate(flags);
    let mut allocator = if flags.contains(ClvmFlags::LIMIT_HEAP) {
        Allocator::new_limited(500000000)
    } else {
        Allocator::new()
    };

    let r: Response = (|| -> PyResult<Response> {
        let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
        let args = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
        let dialect = ChiaDialect::new(flags);

        Ok(py.detach(|| run_program(&mut allocator, &dialect, program, args, max_cost)))
    })()?;
    adapt_response(py, allocator, r)
}
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
