### Title
Missing `ENABLE_SECP_OPS` Flag Check in 4-Byte Secp Opcode Dispatch Allows Bypassing Hard-Fork Activation Gate — (File: `src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` in `src/chia_dialect.rs` dispatches the secp256k1 and secp256r1 signature-verification operators through **two separate code paths**: a 1-byte opcode path (opcodes 64/65) that correctly checks `ENABLE_SECP_OPS`, and a 4-byte opcode path (opcodes `0x13d61f00`/`0x1c3a8f00`) that **omits the flag check entirely**. An attacker-controlled CLVM program can invoke real secp signature verification via the 4-byte encoding even when the hard-fork has not activated, bypassing the activation gate and causing consensus divergence.

---

### Finding Description

`ClvmFlags::ENABLE_SECP_OPS` is defined as the hard-fork flag that gates secp operator availability: [1](#0-0) 

The 1-byte opcode dispatch correctly guards opcodes 64 and 65 behind this flag: [2](#0-1) 

However, the 4-byte opcode dispatch path — which handles the same two operators under their original 4-byte encodings — performs **no flag check at all**: [3](#0-2) 

Both paths call the identical underlying functions `op_secp256k1_verify` and `op_secp256r1_verify`: [4](#0-3) [5](#0-4) 

The 4-byte path is reached whenever the operator atom has length 4, which is fully attacker-controlled via the CLVM program bytes. The comment at line 158 labels this section "unknown operators with assigned cost," but the secp opcodes are explicitly matched and dispatched to real cryptographic implementations — not treated as unknown no-ops. [6](#0-5) 

---

### Impact Explanation

**Consensus divergence / hard-fork activation bypass.**

When `ENABLE_SECP_OPS` is absent (pre-activation state), a node is supposed to treat secp operations as unknown operators (returning nil with a cost). Instead, via the 4-byte opcode encoding, the node executes real secp signature verification. A CLVM program that uses `0x13d61f00` or `0x1c3a8f00` as its operator atom will:

- **Succeed or fail based on cryptographic validity** on all nodes (flag set or not), because the 4-byte path is unconditional.
- **Succeed or fail based on cryptographic validity** on flag-set nodes via 1-byte opcodes 64/65.
- **Return nil (unknown op)** on non-flag nodes via 1-byte opcodes 64/65.

This means the same logical secp check, expressed via the 4-byte encoding, produces **identical results on pre- and post-activation nodes**, defeating the purpose of the staged hard-fork rollout. A transaction that should be invalid pre-activation (because secp ops are unknown) is instead validated, creating a chain split between nodes that have and have not set `ENABLE_SECP_OPS`.

The corrupted result is: `op_secp256k1_verify`/`op_secp256r1_verify` returns `Ok(Reduction(cost, nil))` (valid signature) or raises `Secp256Failed` on a node where it should have returned `nil` as an unknown operator.

---

### Likelihood Explanation

**High.** The entry path requires only attacker-controlled CLVM bytes — the standard attack surface for any Chia transaction. The attacker encodes the operator atom as the 4-byte value `0x13d61f00` (secp256k1) or `0x1c3a8f00` (secp256r1). No special privileges, social engineering, or compromised infrastructure are needed. The 4-byte opcode format is a documented part of the CLVM encoding scheme, so crafting such a program is straightforward.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch path, mirroring the guard already present for the 1-byte aliases:

```rust
// in the op_len == 4 branch:
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures that before the hard-fork activates, both the 1-byte and 4-byte secp opcode encodings are treated as unknown operators, maintaining consistent consensus behavior across all nodes regardless of which encoding an attacker chooses. [3](#0-2) 

---

### Proof of Concept

1. Construct a CLVM program whose operator atom is the 4-byte big-endian value `0x13d61f00` with arguments `(pubkey msg sig)` where `sig` is a valid secp256k1 signature over `msg` by `pubkey`.
2. Run the program via `run_program` with a `ChiaDialect` that does **not** include `ENABLE_SECP_OPS` in its flags (pre-activation state).
3. **Expected (correct) behavior**: the 4-byte opcode is an unknown operator; it returns `nil` with a cost computed by the unknown-operator cost formula.
4. **Actual behavior**: `op_secp256k1_verify` executes, verifies the signature, and returns `Ok(Reduction(1300000, nil))` — the same result as on a post-activation node.
5. Repeat with `0x1c3a8f00` for secp256r1 (cost 1850000).

The 1-byte opcode 64 with the same arguments and the same flags correctly falls through to `unknown_operator`, confirming the inconsistency is specific to the missing flag check in the 4-byte dispatch path. [7](#0-6)

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
```

**File:** src/chia_dialect.rs (L157-183)
```rust
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

**File:** src/chia_dialect.rs (L246-252)
```rust
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
            _ => {
                return unknown_operator(allocator, o, argument_list, flags, max_cost);
            }
```

**File:** src/secp_ops.rs (L15-21)
```rust
pub fn op_secp256r1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256R1_VERIFY_COST;
```

**File:** src/secp_ops.rs (L61-68)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256K1_VERIFY_COST;
    check_cost(cost, max_cost)?;
```
