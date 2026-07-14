### Title
`RuntimeDialect::softfork_extension` Always Returns `OperatorSet::Default`, Silently Discarding Softfork Programs - (File: src/runtime_dialect.rs)

### Summary

`RuntimeDialect::softfork_extension` unconditionally returns `OperatorSet::Default` for every extension number, ignoring its input entirely. This is the direct analog of the reported `calcMint` bug: a routing function that should dispatch to different operator sets based on its argument instead always returns the hardcoded default. In consensus mode (without `NO_UNKNOWN_OPS`), this causes every softfork guard — including the live extension 0 (BLS) and extension 1 (Keccak) guards — to be silently swallowed and replaced with `nil`, rather than being executed.

### Finding Description

`ChiaDialect::softfork_extension` correctly maps extension numbers to operator sets:

```rust
// src/chia_dialect.rs lines 269-283
fn softfork_extension(&self, ext: u32) -> OperatorSet {
    match ext {
        0 => OperatorSet::Bls,
        1 => OperatorSet::Keccak,
        _ => OperatorSet::Default,
    }
}
```

`RuntimeDialect::softfork_extension` performs no such dispatch:

```rust
// src/runtime_dialect.rs lines 73-75
fn softfork_extension(&self, _ext: u32) -> OperatorSet {
    OperatorSet::Default
}
```

The parameter is named `_ext`, confirming the input is intentionally discarded. Every extension — including 0 and 1 — returns `OperatorSet::Default`.

`run_program.rs` treats `OperatorSet::Default` from `softfork_extension` as an unknown extension and raises `EvalErr::UnknownSoftforkExtension`:

```rust
// src/run_program.rs lines 363-367
if extension == OperatorSet::Default {
    Err(EvalErr::UnknownSoftforkExtension)
} else {
    Ok((extension, program, env))
}
```

In consensus mode (`allow_unknown_ops() == true`), this error is caught and silently replaced with `nil` at the expected cost:

```rust
// src/run_program.rs lines 400-412
let (ext, prg, env) = match self.parse_softfork_arguments(operand_list) {
    Ok(ret_values) => ret_values,
    Err(err) => {
        if self.dialect.allow_unknown_ops() {
            self.push(self.allocator.nil())?;
            return Ok(expected_cost);
        }
        return Err(err);
    }
};
```

The softfork program body is never evaluated. The guard exits with `nil` and the declared cost, regardless of what the program would have computed.

### Impact Explanation

Any caller that constructs a `RuntimeDialect` in consensus mode (i.e., without `ClvmFlags::NO_UNKNOWN_OPS`) and runs a program containing a `softfork` call with extension 0 or 1 will receive `nil` as the result of that guard instead of the correct output. BLS signature verification programs and Keccak hash programs inside softfork guards are silently no-oped. A transaction whose validity depends on a softfork guard returning a non-nil value (e.g., a BLS pairing check) will be incorrectly accepted. This is a consensus-divergence class bug: nodes using `ChiaDialect` correctly execute the guard; nodes or Python callers using `RuntimeDialect` silently accept it.

### Likelihood Explanation

`RuntimeDialect` is the dialect exposed to Python callers who supply a custom opcode map (e.g., via the `wheel/` PyO3 bindings). Any Python-side validator or generator runner that constructs a `RuntimeDialect` and processes real Chia programs containing softfork guards is affected. The entry path is fully attacker-controlled: a crafted CLVM program with a `(softfork cost 0 program env)` call is sufficient to trigger the silent discard.

### Recommendation

`RuntimeDialect::softfork_extension` should mirror the dispatch logic of `ChiaDialect::softfork_extension`, mapping extension 0 to `OperatorSet::Bls` and extension 1 to `OperatorSet::Keccak`, and returning `OperatorSet::Default` only for unknown extensions. Alternatively, if `RuntimeDialect` is intentionally restricted to no softfork support, it should set `allow_unknown_ops()` to `false` unconditionally so that unknown softfork extensions produce a hard error rather than a silent `nil`.

### Proof of Concept

1. Construct a `RuntimeDialect` without `NO_UNKNOWN_OPS` (consensus mode).
2. Run a program: `(softfork 100 0 (bls_verify pubkey msg sig) ())`.
3. With `ChiaDialect`, the BLS verification executes and returns `nil` on success or raises on failure.
4. With `RuntimeDialect`, `softfork_extension(0)` returns `OperatorSet::Default`, `parse_softfork_arguments` raises `UnknownSoftforkExtension`, `allow_unknown_ops()` is `true`, so `nil` is pushed and cost 100 is charged — the BLS program body is never run.
5. A transaction that should be rejected (invalid BLS signature inside a softfork guard) is instead accepted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/runtime_dialect.rs (L73-75)
```rust
    fn softfork_extension(&self, _ext: u32) -> OperatorSet {
        OperatorSet::Default
    }
```

**File:** src/chia_dialect.rs (L269-283)
```rust
    fn softfork_extension(&self, ext: u32) -> OperatorSet {
        match ext {
            // Extension 0 is for the BLS operators, and is still valid.
            // However, the extension doesn't add any addition opcodes,
            // because the BLS operators were hardforked into the main set.
            0 => OperatorSet::Bls,

            // Extension 1 is for the keccak256 operator.
            1 => OperatorSet::Keccak,

            // Extensions 2 and beyond are considered invalid by the mempool.
            // However, all future extensions are valid in consensus mode and reserved for future softforks.
            _ => OperatorSet::Default,
        }
    }
```

**File:** src/run_program.rs (L354-368)
```rust
    fn parse_softfork_arguments(&self, args: NodePtr) -> Result<(OperatorSet, NodePtr, NodePtr)> {
        let [_cost, extension, program, env] = get_args::<4>(self.allocator, args, "softfork")?;

        let extension = self.dialect.softfork_extension(uint_atom::<4>(
            self.allocator,
            extension,
            "softfork",
            self.dialect.flags(),
        )? as u32);
        if extension == OperatorSet::Default {
            Err(EvalErr::UnknownSoftforkExtension)
        } else {
            Ok((extension, program, env))
        }
    }
```

**File:** src/run_program.rs (L400-413)
```rust
            let (ext, prg, env) = match self.parse_softfork_arguments(operand_list) {
                Ok(ret_values) => ret_values,
                Err(err) => {
                    if self.dialect.allow_unknown_ops() {
                        // In this case, we encountered a softfork invocation
                        // that doesn't pass the correct arguments.
                        // if we're in consensus mode, we have to accept this as
                        // something we don't understand
                        self.push(self.allocator.nil())?;
                        return Ok(expected_cost);
                    }
                    return Err(err);
                }
            };
```
