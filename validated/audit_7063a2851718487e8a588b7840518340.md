### Title
`isIsolatedSubaccountActive` Returns True for Uninitialized (`bytes32(0)`) Slots, Enabling False Positive Membership Match — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.isIsolatedSubaccountActive` iterates over the `isolatedSubaccounts` mapping and returns `true` the moment any slot equals the queried `subaccount`. It does not exclude uninitialized (default `bytes32(0)`) slots. Because Solidity initializes all storage to zero, every unregistered slot in the array equals `bytes32(0)`. Passing `bytes32(0)` as the `subaccount` argument therefore always returns `true` for any `parent`, regardless of whether any isolated subaccount was ever created. `bytes32(0)` is the protocol's `FEES_ACCOUNT`.

---

### Finding Description

`isIsolatedSubaccountActive` loops over `MAX_ISOLATED_SUBACCOUNTS_PER_ADDRESS` (10) slots and returns `true` on the first match:

```solidity
function isIsolatedSubaccountActive(bytes32 parent, bytes32 subaccount)
    external view returns (bool)
{
    for (uint256 id = 0; id < MAX_ISOLATED_SUBACCOUNTS_PER_ADDRESS; id++) {
        if (subaccount == isolatedSubaccounts[parent][id]) {  // ← no zero-check
            return true;
        }
    }
    return false;
}
``` [1](#0-0) 

The sibling function `getIsolatedSubaccounts` correctly guards against this by explicitly skipping zero-valued slots:

```solidity
if (isolatedSubaccount != bytes32(0)) {
    nIsolatedSubaccounts += 1;
}
``` [2](#0-1) 

`isIsolatedSubaccountActive` has no equivalent guard. Since `isolatedSubaccounts[parent][id]` is `bytes32(0)` for every slot that has never been written, querying with `subaccount = bytes32(0)` will match slot `id = 0` immediately and return `true` for **any** `parent`.

The protocol defines `bytes32(0)` as `FEES_ACCOUNT`:

```solidity
bytes32 constant FEES_ACCOUNT = bytes32(0);
``` [3](#0-2) 

`isIsolatedSubaccountActive` is called in `Clearinghouse.sol` (2 call sites), which is the central risk and settlement engine. Any gate in `Clearinghouse` that relies on this function to confirm a subaccount is a valid isolated subaccount before performing a privileged operation (collateral transfer, settlement, liquidation routing) can be bypassed by supplying `bytes32(0)` as the subaccount. [1](#0-0) 

---

### Impact Explanation

An attacker who can invoke any `Clearinghouse` path that calls `isIsolatedSubaccountActive` with an attacker-supplied `subaccount` value can pass `bytes32(0)` and receive a `true` result unconditionally. Because `bytes32(0)` is `FEES_ACCOUNT` — the protocol's fee-collection account — a false positive here could allow:

- Treating `FEES_ACCOUNT` as a valid isolated subaccount of an arbitrary parent, enabling unauthorized collateral or settlement operations against it.
- Bypassing the isolated-subaccount membership invariant that `Clearinghouse` enforces before routing liquidation or margin operations.

The corrupted invariant is: *"a subaccount returned as active must have been explicitly registered in `isolatedSubaccounts[parent][id]` with a non-zero value."*

---

### Likelihood Explanation

The entry path is externally reachable: any caller (trader, liquidator) who can submit a transaction that reaches a `Clearinghouse` function gated by `isIsolatedSubaccountActive` can supply `bytes32(0)` as the subaccount. The false positive is deterministic — it requires no special state, no privileged role, and no race condition. The only prerequisite is that the caller can reach the relevant `Clearinghouse` entry point with a `bytes32(0)` subaccount argument.

---

### Recommendation

Add an explicit zero-check at the top of `isIsolatedSubaccountActive`, consistent with the guard already present in `getIsolatedSubaccounts`:

```solidity
function isIsolatedSubaccountActive(bytes32 parent, bytes32 subaccount)
    external view returns (bool)
{
    if (subaccount == bytes32(0)) return false;  // exclude uninitialized slots
    for (uint256 id = 0; id < MAX_ISOLATED_SUBACCOUNTS_PER_ADDRESS; id++) {
        if (subaccount == isolatedSubaccounts[parent][id]) {
            return true;
        }
    }
    return false;
}
``` [1](#0-0) 

---

### Proof of Concept

1. No isolated subaccounts have been created for `parent = 0xABCD...`.
2. All 10 slots in `isolatedSubaccounts[0xABCD...][0..9]` are `bytes32(0)` (Solidity default).
3. Call `isIsolatedSubaccountActive(0xABCD..., bytes32(0))`.
4. Loop iteration `id = 0`: `bytes32(0) == isolatedSubaccounts[0xABCD...][0]` → `bytes32(0) == bytes32(0)` → `true`.
5. Function returns `true`, falsely asserting that `FEES_ACCOUNT` (`bytes32(0)`) is an active isolated subaccount of `0xABCD...`.
6. Any `Clearinghouse` logic gated on this result proceeds as if a valid isolated subaccount relationship exists, enabling unauthorized state changes against `FEES_ACCOUNT`. [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/OffchainExchange.sol (L1099-1101)
```text
            bytes32 isolatedSubaccount = isolatedSubaccounts[subaccount][id];
            if (isolatedSubaccount != bytes32(0)) {
                nIsolatedSubaccounts += 1;
```

**File:** core/contracts/OffchainExchange.sol (L1118-1129)
```text
    function isIsolatedSubaccountActive(bytes32 parent, bytes32 subaccount)
        external
        view
        returns (bool)
    {
        for (uint256 id = 0; id < MAX_ISOLATED_SUBACCOUNTS_PER_ADDRESS; id++) {
            if (subaccount == isolatedSubaccounts[parent][id]) {
                return true;
            }
        }
        return false;
    }
```

**File:** core/contracts/common/Constants.sol (L8-8)
```text
bytes32 constant FEES_ACCOUNT = bytes32(0);
```
