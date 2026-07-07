### Title
Unrestricted `submitTransactionsCheckedWithGasLimit` Leaks Internal Gas Telemetry to Any Caller via `revertGasInfo` — (File: core/contracts/Endpoint.sol)

---

### Summary

`submitTransactionsCheckedWithGasLimit` in `Endpoint.sol` is missing the `require(msg.sender == sequencer)` access control and Schnorr signature verification that its sibling `submitTransactionsChecked` enforces. Any unprivileged caller can invoke it with arbitrary transaction batches and receive precise per-transaction gas usage data through the `revertGasInfo` revert string — the direct Solidity analog to returning `str(e)` in a JSON error response.

---

### Finding Description

`submitTransactionsChecked` enforces two guards before processing transactions:

```solidity
validateSubmissionIdx(idx);
require(msg.sender == sequencer);          // caller restriction
verifier.requireValidSignature(...);       // Schnorr quorum check
```

`submitTransactionsCheckedWithGasLimit` enforces only the submission index check:

```solidity
function submitTransactionsCheckedWithGasLimit(
    uint64 idx,
    bytes[] calldata transactions,
    uint256 gasLimit
) external {
    uint256 initialGas = gasleft();
    validateSubmissionIdx(idx);            // only guard
    for (uint256 i = 0; i < transactions.length; i++) {
        processTransaction(transaction);
        uint256 gasUsed = initialGas - gasleft();
        if (gasUsed > gasLimit) {
            verifier.revertGasInfo(i, gasUsed);   // early revert with telemetry
        }
    }
    verifier.revertGasInfo(transactions.length, initialGas - gasleft()); // always reverts
}
```

`revertGasInfo` in `Verifier.sol` unconditionally reverts with a human-readable string encoding the transaction index and cumulative gas consumed:

```solidity
function revertGasInfo(uint256 i, uint256 gasUsed) external pure {
    revert(
        string.concat("G ", MathHelper.uint2str(uint128(i)),
                       " ", MathHelper.uint2str(uint128(gasUsed)))
    );
}
```

Because the function always reverts, no state change persists. But the revert payload is returned to the caller as raw internal execution data — exactly the pattern the external report flags.

The submission index barrier is trivially bypassed: `nSubmissions` is declared `public` in `EndpointStorage.sol`, so any caller can read the current value and supply the correct `idx`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

Gas usage is a side-channel that encodes internal branching decisions. An attacker who can submit arbitrary transactions and observe the resulting gas figure can determine:

- **Subaccount existence**: `requireSubaccount` performs a cold SLOAD on `subaccountIds[subaccount]`; the gas delta between a known-registered and an unknown subaccount is measurable.
- **Health status**: `_isAboveInitial` / `getHealth` traverses all engine balances; the number of products held by a target subaccount changes the gas cost, leaking position count and approximate portfolio composition.
- **Liquidation eligibility**: A crafted `LiquidateSubaccount` transaction will consume different gas depending on whether the target is actually underwater, revealing health state without executing the liquidation.
- **Nonce state**: `validateNonce` reverts immediately on a wrong nonce; the gas difference between a correct and incorrect nonce is observable, enabling nonce enumeration.

Because the function always reverts, the attacker pays only the gas for the simulation call and leaves no on-chain trace. The disclosed telemetry enables precise timing of liquidations, front-running of withdrawals, and targeted construction of follow-on transactions. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

- `nSubmissions` is `public`; the required `idx` is always readable.
- No token approval, deposit, or privileged role is needed.
- The call can be made as `eth_call` (zero cost) or as a regular transaction; either way the revert data is returned to the caller.
- The function is present in the deployed ABI (`revertGasInfo` appears in `core/abi/Verifier.json`), confirming it is part of the production surface. [4](#0-3) 

---

### Recommendation

1. Add `require(msg.sender == sequencer, ERR_UNAUTHORIZED)` to `submitTransactionsCheckedWithGasLimit`, matching the guard in `submitTransactionsChecked`.
2. If the function is intended only for off-chain gas estimation (i.e., always called via `eth_call`), document this explicitly and add the sequencer guard anyway to prevent on-chain abuse.
3. Consider whether `revertGasInfo` needs to be `external`; if it is only called internally by `Endpoint`, restrict its visibility to prevent direct invocation. [7](#0-6) [8](#0-7) 

---

### Proof of Concept

```
// Attacker reads current submission index (public getter)
uint64 idx = endpoint.nSubmissions();

// Craft a LiquidateSubaccount transaction targeting victim
bytes memory probeTx = abi.encodePacked(
    uint8(IEndpoint.TransactionType.LiquidateSubaccount),
    abi.encode(signedLiquidateTx)
);

// Call with gasLimit = 0 to force immediate revert with gas data
// This is a staticcall / eth_call — zero cost, no state change
(bool ok, bytes memory ret) = address(endpoint).call(
    abi.encodeWithSelector(
        Endpoint.submitTransactionsCheckedWithGasLimit.selector,
        idx,
        [probeTx],
        uint256(0)
    )
);
// ok == false; ret contains "G 0 <gasUsed>"
// Parse gasUsed to determine whether victim subaccount is healthy or underwater
// Repeat with different victims or transaction types to enumerate state
```

The revert string `"G 0 <gasUsed>"` is decoded from `ret` by stripping the 4-byte ABI error selector and parsing the ASCII digits — identical in structure to reading `{"error": "..."}` from a Python API response. [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }
```

**File:** core/contracts/Endpoint.sol (L296-312)
```text
    function submitTransactionsCheckedWithGasLimit(
        uint64 idx,
        bytes[] calldata transactions,
        uint256 gasLimit
    ) external {
        uint256 initialGas = gasleft();
        validateSubmissionIdx(idx);
        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            uint256 gasUsed = initialGas - gasleft();
            if (gasUsed > gasLimit) {
                verifier.revertGasInfo(i, gasUsed);
            }
        }
        verifier.revertGasInfo(transactions.length, initialGas - gasleft());
    }
```

**File:** core/contracts/Verifier.sol (L50-59)
```text
    function revertGasInfo(uint256 i, uint256 gasUsed) external pure {
        revert(
            string.concat(
                "G ",
                MathHelper.uint2str(uint128(i)),
                " ",
                MathHelper.uint2str(uint128(gasUsed))
            )
        );
    }
```

**File:** core/contracts/EndpointStorage.sol (L36-36)
```text
    uint64 public nSubmissions;
```

**File:** core/contracts/EndpointStorage.sol (L74-81)
```text
    function requireSubaccount(bytes32 subaccount) internal view {
        require(
            subaccount == X_ACCOUNT ||
                subaccount == N_ACCOUNT ||
                (subaccountIds[subaccount] != 0),
            ERR_REQUIRES_DEPOSIT
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
```
