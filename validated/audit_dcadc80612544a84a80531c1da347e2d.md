### Title
`minIdx` in `BaseWithdrawPool` Is Monotonically Increasing with No Reset Mechanism, Permanently Blocking Valid Fast Withdrawals — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

The `minIdx` state variable in `BaseWithdrawPool` is set to the `nSubmissions` index of the most recent standard withdrawal processed by the sequencer. Because `nSubmissions` only ever increases, `minIdx` only ever increases. `submitFastWithdrawal` enforces `idx > minIdx`, meaning any fast withdrawal authorization signed at an earlier submission index is permanently and irrecoverably blocked. No function exists to decrease or reset `minIdx`.

---

### Finding Description

`submitWithdrawal` is the sequencer-driven standard withdrawal path. It is called by the clearinghouse and unconditionally overwrites `minIdx` with the `idx` argument:

```solidity
// BaseWithdrawPool.sol L116-132
function submitWithdrawal(
    IERC20Base token,
    address sendTo,
    uint128 amount,
    uint64 idx
) public {
    require(msg.sender == clearinghouse);
    if (markedIdxs[idx]) { return; }
    markedIdxs[idx] = true;
    minIdx = idx;          // <-- always increases
    handleWithdrawTransfer(token, sendTo, amount);
}
``` [1](#0-0) 

The `idx` passed here is `nSubmissions` at the moment the withdrawal transaction is processed by the Endpoint:

```solidity
// EndpointTx.sol L430-436
clearinghouse.withdrawCollateral(
    signedTx.tx.sender,
    signedTx.tx.productId,
    signedTx.tx.amount,
    address(0),
    nSubmissions          // <-- idx = current nSubmissions
);
``` [2](#0-1) 

`nSubmissions` is a `uint64` that is incremented by 1 for every transaction processed through `submitTransactionsChecked` and `executeSlowModeTransaction`, and is never decremented: [3](#0-2) [4](#0-3) 

`submitFastWithdrawal` then enforces:

```solidity
// BaseWithdrawPool.sol L86-88
require(!markedIdxs

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
```

**File:** core/contracts/EndpointTx.sol (L430-436)
```text
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/Endpoint.sol (L289-293)
```text
        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
```

**File:** core/contracts/EndpointStorage.sol (L36-36)
```text
    uint64 public nSubmissions;
```
