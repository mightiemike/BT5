### Title
ML-DSA-65 gas-key fee divergence between transaction path and host-function path — (`runtime/runtime/src/config.rs` / `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

`gas_key_transfer_send_fee` and `gas_key_transfer_exec_fee` are called with two different representations of the public-key length depending on whether the action originates from a signed transaction or from a contract host-function call. For ed25519/secp256k1 keys the two representations are identical, so the divergence is invisible. For ML-DSA-65 keys the borsh-encoded wire length is 1953 bytes while the on-trie identifier length (`trie_id_len()`) is 33 bytes — a ~59× difference. Any contract that calls `promise_batch_action_transfer_to_gas_key` (or the equivalent `WithdrawFromGasKey` / `AddKey`-gas-key host functions) with an ML-DSA-65 key is charged ~59× more gas for the per-byte component than the identical action submitted as a signed transaction, making the host-function path for ML-DSA-65 gas keys non-functional in practice.

---

### Finding Description

**Transaction path** — `runtime/runtime/src/config.rs`, `total_send_fees` and `exec_fee`:

```rust
// line 114-116
TransferToGasKey(action) => {
    gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
        .total()
}
// line 347-350
TransferToGasKey(action) => {
    gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
        .total()
}
```

Both send and exec fees are computed with `action.public_key.trie_id_len()`, which for ML-DSA-65 returns `1 + ML_DSA_65_HASH_LENGTH = 33`.

**Host-function path** — `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`, `promise_batch_action_transfer_to_gas_key`:

```rust
// line 3323-3325
let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
let exec =
    gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```

`public_key_len` is the raw byte count supplied by the contract to the host function — for an ML-DSA-65 key this is the borsh-encoded full-pubkey length: `1 + ML_DSA_65_PUBLIC_KEY_LENGTH = 1953`.

The same pattern repeats in `runtime/near-vm-runner/src/logic/logic.rs` for `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call`, where `gas_key_add_key_exec_fee` is called with `public_key_len as usize` instead of `public_key.trie_id_len()`.

The `gas_key_transfer_exec_fee` function's own doc comment states it is "Based on the access key trie key length + estimated value length (what the receiver needs to read/write in the trie)." The trie key for an ML-DSA-65 access key uses the 32-byte SHA3-256 hash (33 bytes with tag), not the 1952-byte full pubkey. The exec fee is therefore structurally required to use `trie_id_len()`, not the wire length.

**Exact divergent values for ML-DSA-65:**

| Path | `public_key_len` passed | Per-byte fee multiplier |
|---|---|---|
| Signed transaction | 33 (`trie_id_len()`) | 1× |
| Host function | 1953 (borsh wire length) | ~59× |

For ed25519 and secp256k1, `len() == trie_id_len()` (33 and 65 bytes respectively), so no divergence exists for those key types.

---

### Impact Explanation

Once `PostQuantumSignatures` is active (protocol version 85), any contract that attempts to call `promise_batch_action_transfer_to_gas_key` or `promise_batch_action_add_gas_key_with_full_access` / `promise_batch_action_add_gas_key_with_function_call` with an ML-DSA-65 key will be charged ~59× the expected per-byte gas for the action. The contract will exhaust its prepaid gas and the action will fail. Because the fee divergence is baked into the protocol-level gas accounting (not a contract-level bug), no amount of extra gas attached by the caller can make the host-function path match the transaction path's cost model — the two paths produce different `gas_burnt` values for identical on-chain effects, breaking the fee-domain invariant that "the same action costs the same gas regardless of how it is submitted." This renders the ML-DSA-65 gas-key feature non-functional via host functions.

---

### Likelihood Explanation

The `PostQuantumSignatures` feature is stabilized at protocol version 85. Once that version is live, any unprivileged user can create an ML-DSA-65 access key and attempt to use it as a gas key from a contract. The divergence is triggered by the ordinary host-function call path with no special preconditions beyond key type. The only mitigating factor is that ML-DSA-65 gas keys are a new feature and may not yet be widely used.

---

### Recommendation

Replace `public_key_len as usize` with `public_key.trie_id_len()` in every host-function site that calls `gas_key_transfer_send_fee`, `gas_key_transfer_exec_fee`, and `gas_key_add_key_exec_fee`. Specifically:

- `runtime/near-vm-runner/src/wasmtime_runner/logic.rs` — `promise_batch_action_transfer_to_gas_key` (lines 3323–3325): after decoding the public key, use `public_key.decode()?.trie_id_len()` for both send and exec fee calls.
- `runtime/near-vm-runner/src/logic/logic.rs` — `promise_batch_action_add_gas_key_with_full_access` (line 3158) and `promise_batch_action_add_gas_key_with_function_call` (line 3229): replace `public_key_len as usize` with the decoded key's `trie_id_len()`.

Add a test analogous to `test_gas_key_fee_parity` that exercises ML-DSA-65 keys and asserts that `gas_burnt` is identical between the transaction path and the host-function path.

---

### Proof of Concept

1. Activate `PostQuantumSignatures` (protocol version ≥ 85).
2. Generate an ML-DSA-65 key pair; add it as a `GasKeyFullAccess` key on account `alice` via a signed `AddKey` transaction.
3. Deploy a contract on `alice` that calls `promise_batch_action_transfer_to_gas_key` targeting the ML-DSA-65 key with a small deposit.
4. Submit the contract call with the same prepaid gas as an equivalent signed `TransferToGasKey` transaction.
5. Observe: the contract call fails with `GasExceeded` while the signed transaction succeeds, because the host-function path charges `gas_key_byte_exec * 1953` while the transaction path charges `gas_key_byte_exec * 33` for the per-byte exec component.

The divergence is directly visible by comparing `gas_burnt` on the execution outcome of the two paths — the host-function path burns ~59× more gas for the per-byte component of the action fee.

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** runtime/runtime/src/config.rs (L114-116)
```rust
            TransferToGasKey(action) => {
                gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
                    .total()
```

**File:** runtime/runtime/src/config.rs (L347-350)
```rust
        TransferToGasKey(action) => {
            gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
                .total()
        }
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3323-3325)
```rust
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
    let exec =
        gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```

**File:** core/parameters/src/cost.rs (L814-847)
```rust
/// Send fee for TransferToGasKey / WithdrawFromGasKey actions.
/// Based on the public key length (what the sender sees).
pub fn gas_key_transfer_send_fee(
    cfg: &RuntimeFeesConfig,
    sender_is_receiver: bool,
    public_key_len: usize,
) -> GasKeyTransferFee {
    let base = cfg.fee(ActionCosts::gas_key_transfer_base).send_fee(sender_is_receiver);
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .send_fee(sender_is_receiver)
        .checked_mul(public_key_len as u64)
        .unwrap();
    GasKeyTransferFee { base, per_byte }
}

/// Exec fee for TransferToGasKey / WithdrawFromGasKey actions.
/// Based on the access key trie key length + estimated value length (what the
/// receiver needs to read/write in the trie).
pub fn gas_key_transfer_exec_fee(
    cfg: &RuntimeFeesConfig,
    account_id_len: usize,
    public_key_len: usize,
) -> GasKeyTransferFee {
    let base = cfg.fee(ActionCosts::gas_key_transfer_base).exec_fee();
    let trie_key_len = access_key_key_len(account_id_len, public_key_len);
    let estimated_value_len = AccessKey::min_gas_key_borsh_len();
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .exec_fee()
        .checked_mul((trie_key_len + estimated_value_len) as u64)
        .unwrap();
    GasKeyTransferFee { base, per_byte }
}
```

**File:** core/crypto/src/signature.rs (L333-339)
```rust
    pub fn trie_id_len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_HASH_LENGTH,
        }
    }
```
