### Title
`ENABLE_SECP_OPS` Flag Guard Bypassed via 4-Byte Opcode Aliases — (`File: src/chia_dialect.rs`)

---

### Summary

The `ChiaDialect::op()` function in `src/chia_dialect.rs` gates `op_secp256k1_verify` and `op_secp256r1_verify` behind the `ENABLE_SECP_OPS` flag when dispatched through 1-byte opcodes 64 and 65. However, the identical functions are also reachable through 4-byte opcode aliases `0x13d61f00` and `0x1c3a8f00` in a separate dispatch branch that performs **no flag check at all**. An attacker-controlled CLVM program can invoke secp signature verification unconditionally by using the 4-byte encoding, bypassing the soft-fork activation gate entirely.

---

### Finding Description

`ChiaDialect::op()` contains two structurally separate dispatch branches:

**Branch 1 — 4-byte opcodes (lines 157–183):** Entered when `op_len == 4`. The two secp opcodes are matched and dispatched with no flag guard:

```rust
// src/chia_dialect.rs lines 175–182
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
return f(allocator, argument_list, max_cost, flags);
``` [1](#0-0) 

**Branch 2 — 1-byte opcodes (lines 248–249):** Entered when `op_len == 1`. The same functions are dispatched only when `ENABLE_SECP_OPS` is set:

```rust
// src/chia_dialect.rs lines 248–249
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

The flag is defined as a soft-fork activation gate:

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [3](#0-2) 

The 4-byte branch is entered first (before the 1-byte branch) and returns immediately after dispatching, so the `ENABLE_SECP_OPS` check in the 1-byte branch is never reached when the 4-byte encoding is used. [4](#0-3) 

The underlying operator implementations `op_secp256k1_verify` and `op_secp256r1_verify` are identical regardless of which dispatch path is used: [5](#0-4) 

---

### Impact Explanation

**Impact: Medium**

Any caller that omits `ENABLE_SECP_OPS` from its `ClvmFlags` — intending to prevent secp signature verification from executing — can have that restriction bypassed by a CLVM program that uses the 4-byte opcode encoding. The concrete corrupted result is: `op_secp256k1_verify` or `op_secp256r1_verify` executes and returns a valid `Reduction` (or a `Secp256Failed` error) when the caller's flag configuration mandates it should be treated as an unknown operator. This breaks the invariant that `ENABLE_SECP_OPS` is a complete activation gate for secp operations.

The Python API exposes `ENABLE_SECP_OPS` as a public constant for callers to use as a control flag: [6](#0-5) 

Any Python or Rust caller relying on the absence of this flag to enforce pre-soft-fork consensus rules is vulnerable.

---

### Likelihood Explanation

**Likelihood: High**

The 4-byte opcode values `0x13d61f00` and `0x1c3a8f00` are documented in the source code comments and appear in the fuzzing infrastructure and benchmark tooling: [7](#0-6) 

The bypass requires only crafting a CLVM atom of exactly 4 bytes with the correct value as the operator — a trivial operation for any attacker who can submit CLVM programs. No special privileges, social engineering, or dependency compromise is required.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch branch, mirroring the guard already present for the 1-byte aliases:

```rust
// In the op_len == 4 branch:
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures that both dispatch paths enforce the same activation gate, eliminating the inconsistency.

---

### Proof of Concept

The existing test suite in `src/run_program.rs` already demonstrates the divergence. The test at lines 1312–1318 shows `secp256k1_verify` (the 4-byte opcode form, compiled from the name `secp256k1_verify` which maps to `0x13d61f00`) succeeds with `ClvmFlags::empty()` — i.e., without `ENABLE_SECP_OPS`: [8](#0-7) 

While the test at lines 1365–1373 shows that opcode 64 (the 1-byte alias) is rejected as unimplemented under `NO_UNKNOWN_OPS` without `ENABLE_SECP_OPS`: [9](#0-8) 

A concrete attacker-controlled CLVM program demonstrating the bypass:

```
; Uses 4-byte opcode 0x13d61f00 directly — executes secp256k1_verify
; even when ENABLE_SECP_OPS is NOT set in the dialect flags.
(0x13d61f00
  (q . <33-byte-pubkey>)
  (q . <32-byte-msg-hash>)
  (q . <64-byte-sig>))
```

Running this program with `ChiaDialect::new(ClvmFlags::NO_UNKNOWN_OPS)` (which lacks `ENABLE_SECP_OPS`) will execute the secp verification and return `Reduction(1300000, nil)` on a valid signature — whereas the caller's intent was to treat this as an unimplemented operator. The `f_table.rs` confirms both `op_secp256k1_verify` and `op_secp256r1_verify` are registered and reachable: [10](#0-9)

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
```

**File:** src/chia_dialect.rs (L156-183)
```rust
        let op_len = allocator.atom_len(o);
        if op_len == 4 {
            // these are unknown operators with assigned cost
            // the formula is:
            // +---+---+---+------------+
            // | multiplier|XX | XXXXXX |
            // +---+---+---+---+--------+
            //  ^           ^    ^
            //  |           |    + 6 bits ignored when computing cost
            // cost         |
            // (3 bytes)    + 2 bits
            //                cost_function

            let b = allocator.atom(o);
            let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());

            // the secp operators have a fixed cost of 1850000 and 1300000,
            // which makes the multiplier 0x1c3a8f and 0x0cf84f (there is an
            // implied +1) and cost function 0
            let f = match opcode {
                0x13d61f00 => op_secp256k1_verify,
                0x1c3a8f00 => op_secp256r1_verify,
                _ => {
                    return unknown_operator(allocator, o, argument_list, flags, max_cost);
                }
            };
            return f(allocator, argument_list, max_cost, flags);
        }
```

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** src/secp_ops.rs (L61-103)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256K1_VERIFY_COST;
    check_cost(cost, max_cost)?;

    let [pubkey, msg, sig] = get_args::<3>(a, input, "secp256k1_verify")?;

    // first argument is sec1 encoded pubkey
    let pubkey = atom(a, pubkey, "secp256k1_verify pubkey")?;
    let verifier = K1VerifyingKey::from_sec1_bytes(pubkey.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(input, "secp256k1_verify: pubkey is not valid".to_string())
    })?;

    // second arg is message
    let msg = atom(a, msg, "secp256k1_verify msg")?;
    if msg.as_ref().len() != 32 {
        Err(EvalErr::InvalidOpArg(
            input,
            "secp256k1_verify: message digest is not 32 bytes".to_string(),
        ))?;
    }

    // third arg is a fixed-size signature
    let sig = atom(a, sig, "secp256k1_verify sig")?;
    let sig = K1Signature::from_slice(sig.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(
            input,
            "secp256k1_verify: signature is not valid".to_string(),
        )
    })?;

    // verify signature
    let result = verifier.verify_prehash(msg.as_ref(), &sig);

    if result.is_err() {
        Err(EvalErr::Secp256Failed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
```

**File:** wheel/src/api.rs (L321-321)
```rust
    m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
```

**File:** src/run_program.rs (L1312-1318)
```rust
        RunProgramTest {
            prg: "(secp256k1_verify (q . 0x02888b0c110ef0b4962e3fc6929cbba7a8bb25b4b2c885f55c76365018c909b439) (q . 0x74c2941eb2ebe5aa4f2287a4c5e506a6290c045004058de97a7edf0122548668) (q . 0x1acb7a6e062e78ccd4237b12c22f02b5a8d9b33cb3ba13c35e88e036baa1cbca75253bb9a96ffc48b43196c69c2972d8f965b1baa4e52348d8081cde65e6c018))",
            args: "()",
            flags: ClvmFlags::empty(),
            result: Some("0"),
            cost: 1300061,
            err: "",
```

**File:** src/run_program.rs (L1365-1373)
```rust
        // Opcode 64 without ENABLE_SECP_OPS is unimplemented
        RunProgramTest {
            prg: "(secp256k1_verify_64 (q . 0x02888b0c110ef0b4962e3fc6929cbba7a8bb25b4b2c885f55c76365018c909b439) (q . 0x74c2941eb2ebe5aa4f2287a4c5e506a6290c045004058de97a7edf0122548668) (q . 0x1acb7a6e062e78ccd4237b12c22f02b5a8d9b33cb3ba13c35e88e036baa1cbca75253bb9a96ffc48b43196c69c2972d8f965b1baa4e52348d8081cde65e6c018))",
            args: "()",
            flags: ClvmFlags::NO_UNKNOWN_OPS,
            result: None,
            cost: 0,
            err: "unimplemented operator",
        },
```

**File:** src/f_table.rs (L68-69)
```rust
        (op_secp256k1_verify, "op_secp256k1_verify"),
        (op_secp256r1_verify, "op_secp256r1_verify"),
```
