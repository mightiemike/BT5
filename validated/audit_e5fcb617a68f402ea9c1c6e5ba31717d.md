### Title
`RuntimeDialect::softfork_extension` Always Returns `OperatorSet::Default`, Making the Softfork Operator Permanently Broken — (`File: src/runtime_dialect.rs`)

---

### Summary

`RuntimeDialect` wires up opcode 36 as the `softfork_kw`, but its `softfork_extension` implementation unconditionally returns `OperatorSet::Default`. Because `OperatorSet::Default` is the sentinel value that `parse_softfork_arguments` treats as "unknown extension", the softfork operator can never successfully execute a softfork program under `RuntimeDialect`. In strict mode it always errors; in lenient mode it silently returns nil, discarding the softfork program's result entirely.

---

### Finding Description

In `src/runtime_dialect.rs`, `RuntimeDialect::new()` hardcodes `softfork_kw` to opcode 36, explicitly registering the softfork operator:

```rust
softfork_kw: vec![36], // softfork opcode
``` [1](#0-0) 

However, the `softfork_extension` implementation unconditionally returns `OperatorSet::Default` for every extension value:

```rust
fn softfork_extension(&self, _ext: u32) -> OperatorSet {
    OperatorSet::Default
}
``` [2](#0-1) 

In `src/run_program.rs`, `parse_softfork_arguments` treats `OperatorSet::Default` as the "unknown extension" sentinel and always returns `Err(EvalErr::UnknownSoftforkExtension)`:

```rust
if extension == OperatorSet::Default {
    Err(EvalErr::UnknownSoftforkExtension)
} else {
    Ok((extension, program, env))
}
``` [3](#0-2) 

The caller in `apply_op` then branches on `allow_unknown_ops()`:

- **Strict mode** (`NO_UNKNOWN_OPS` set): the error propagates and the program fails with `UnknownSoftforkExtension`.
- **Lenient mode** (`allow_unknown_ops()` returns `true`): the error is swallowed, nil is pushed onto the value stack, and the softfork program is **never executed**. [4](#0-3) 

This is structurally identical to the `Redeemer.setFee` bug: a function is wired up and reachable, but a prerequisite value (`feeChange` / `softfork_extension`) is never set to a non-sentinel value, so the function can never succeed.

The `OperatorSet` enum confirms that `Default` is the sentinel for "no extension recognized":

```rust
pub enum OperatorSet {
    Default,   // unknown/unrecognized
    Bls,
    Keccak,
}
``` [5](#0-4) 

`ChiaDialect` correctly maps extension 0 → `Bls` and extension 1 → `Keccak`, never returning `Default` for known extensions: [6](#0-5) 

`RuntimeDialect` has no such mapping — it returns `Default` for every input, making the softfork operator permanently broken.

---

### Impact Explanation

Any CLVM program that invokes the softfork operator (opcode 36) under `RuntimeDialect` is affected:

- **Lenient mode**: the softfork program is silently discarded and nil is returned. A program that depends on the softfork guard's side-effects or return value will compute a wrong result with no error signal. This is a silent correctness failure and a potential consensus-divergence vector if `RuntimeDialect` is used in a validation context.
- **Strict mode**: every softfork invocation unconditionally fails with `UnknownSoftforkExtension`, making the softfork operator completely unusable.

The corrupted result is concrete: the softfork program's actual return value is replaced with nil, or the execution is aborted, in all cases.

---

### Likelihood Explanation

`RuntimeDialect` is a public API in the `clvmr` crate, reachable from both Rust callers and (via the Python wheel) Python callers. Any caller that constructs a `RuntimeDialect` and runs a CLVM program containing a softfork invocation will trigger this path. The attacker-controlled entry is the CLVM bytes passed to `run_program`; no special privileges are required. [7](#0-6) 

---

### Recommendation

Implement `softfork_extension` in `RuntimeDialect` to return a meaningful `OperatorSet` for at least the known extensions (0 → `Bls`, 1 → `Keccak`), mirroring `ChiaDialect::softfork_extension`. If softfork is intentionally unsupported in `RuntimeDialect`, remove the `softfork_kw: vec![36]` registration so the operator is not silently recognized and then silently discarded.

---

### Proof of Concept

```
; CLVM program using softfork extension 0 under RuntimeDialect (lenient mode)
; Expected: softfork program executes and returns nil (cost-checked)
; Actual:   softfork_extension returns Default → parse_softfork_arguments errors
;           → allow_unknown_ops() is true → nil pushed, program never runs
(softfork (q . 100) (q . 0) (q . (q . ())) (q . ()))
```

Under `ChiaDialect`, extension 0 maps to `OperatorSet::Bls` and the program executes normally. Under `RuntimeDialect`, `softfork_extension(0)` returns `OperatorSet::Default`, `parse_softfork_arguments` returns `Err(UnknownSoftforkExtension)`, and in lenient mode the softfork program is silently skipped — returning nil without executing the inner program, with no error raised. [2](#0-1) [8](#0-7) [4](#0-3)

### Citations

**File:** src/runtime_dialect.rs (L19-33)
```rust
impl RuntimeDialect {
    pub fn new(
        op_map: HashMap<String, Vec<u8>>,
        quote_kw: Vec<u8>,
        apply_kw: Vec<u8>,
        flags: ClvmFlags,
    ) -> RuntimeDialect {
        RuntimeDialect {
            f_lookup: f_lookup_for_hashmap(op_map),
            quote_kw,
            apply_kw,
            softfork_kw: vec![36], // softfork opcode
            flags,
        }
    }
```

**File:** src/runtime_dialect.rs (L73-75)
```rust
    fn softfork_extension(&self, _ext: u32) -> OperatorSet {
        OperatorSet::Default
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

**File:** src/dialect.rs (L9-20)
```rust
pub enum OperatorSet {
    /// Any softfork extensions that are not added yet will be rejected.
    Default,

    /// Originally added BLS operators when inside softfork extension 0.
    /// The operators have since been hardforked into the main operator set.
    Bls,

    /// The keccak256 operator, which is only available inside the softfork guard.
    /// This uses softfork extension 1, which does not conflict with the BLS fork.
    Keccak,
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
