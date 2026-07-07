### Title
Subsequent Deposits Within Slow-Mode Delay Window Incorrectly Require `MIN_FIRST_DEPOSIT_AMOUNT` — (`File: core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.isValidDepositAmount` determines whether to enforce the higher `MIN_FIRST_DEPOSIT_AMOUNT` ($5) or the lower `MIN_DEPOSIT_AMOUNT` ($0.1) by checking `subaccountIds[subaccount] == 0`. However, `_recordSubaccount` is only called when the slow-mode transaction is **processed** — not when it is **submitted**. Because the slow-mode delay is hardcoded to 3 days, any user who submits a second deposit within that window is incorrectly treated as a brand-new depositor and forced to deposit at least $5 instead of $0.1, a 50× overpayment.

---

### Finding Description

`isValidDepositAmount` selects the minimum deposit threshold based on whether the subaccount has been registered:

```solidity
// Endpoint.sol
function isValidDepositAmount(
    bytes32 subaccount,
    uint32 productId,
    uint128 amount
) internal returns (bool) {
    int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;          // $0.1
    if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
        minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;       // $5
    }
    return clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
}
``` [1](#0-0) 

The two thresholds are:

```solidity
int256 constant MIN_DEPOSIT_AMOUNT       = ONE / 10;  // $0.1
int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE;   // $5
``` [2](#0-1) 

`subaccountIds` is only populated inside `_recordSubaccount`, which is called exclusively during **slow-mode transaction processing** (`processSlowModeTransactionImpl`):

```solidity
// EndpointTx.sol
validateSender(txn.sender, sender);
_recordSubaccount(txn.sender);
clearinghouse.depositCollateral(txn);
``` [3](#0-2) 

`_recordSubaccount` itself only writes to `subaccountIds` if the entry is currently zero:

```solidity
// EndpointStorage.sol
function _recordSubaccount(bytes32 subaccount) internal {
    if (subaccountIds[subaccount] == 0) {
        subaccountIds[subaccount] = ++numSubaccounts;
``` [4](#0-3) 

The slow-mode delay is 3 days:

```solidity
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
``` [5](#0-4) 

When a user calls `depositCollateral`, the slow-mode transaction is enqueued but `_recordSubaccount` is **not** called at submission time:

```solidity
// Endpoint.sol – depositCollateralWithReferral
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    ...
});
``` [6](#0-5) 

Therefore, for the entire 3-day window between submission and processing, `subaccountIds[subaccount]` remains `0`, and every subsequent call to `depositCollateral` or `depositCollateralWithReferral` re-evaluates `isValidDepositAmount` with `MIN_FIRST_DEPOSIT_AMOUNT` instead of `MIN_DEPOSIT_AMOUNT`.

---

### Impact Explanation

A user who has already submitted their first deposit (and whose funds have been transferred to the clearinghouse) is forced to deposit at least $5 for every additional deposit they attempt within the 3-day slow-mode window, even though the protocol's intent is that only the very first deposit requires $5. The correct minimum for all subsequent deposits is $0.1. This is a **50× overpayment** enforced by the contract on legitimate users.

For `depositCollateral` (the direct user-facing entry point), the check is a hard `require`, so the transaction reverts entirely if the user sends less than $5:

```solidity
require(
    isValidDepositAmount(subaccount, productId, amount),
    ERR_DEPOSIT_TOO_SMALL
);
``` [7](#0-6) 

For `depositCollateralWithReferral` (used by `DirectDepositV1`), the deposit is silently skipped, meaning funds that arrive at the contract are not credited to the subaccount at all until the amount reaches $5. [8](#0-7) 

---

### Likelihood Explanation

This is highly likely to occur in normal usage. Any user who:
1. Makes their first deposit, then
2. Wants to top up their account within 3 days (e.g., to meet margin requirements, add collateral for a new position, or simply deposit a second asset)

…will be affected. The 3-day slow-mode delay is a protocol constant, not a configuration choice, making this a systemic issue for all new depositors.

---

### Recommendation

Call `_recordSubaccount` at deposit **submission** time (inside `depositCollateralWithReferral`), not only at processing time. This mirrors the intent of the check: once a user has committed funds and a slow-mode tx is enqueued, they should be treated as an existing depositor for the purpose of minimum deposit validation.

```solidity
// In depositCollateralWithReferral, after isValidDepositAmount passes:
handleDepositTransfer(...);
_recordSubaccount(subaccount);   // <-- add this
slowModeTxs[...] = SlowModeTx({...});
```

`_recordSubaccount` is idempotent (it checks `subaccountIds[subaccount] == 0` internally), so calling it twice — once at submission and once at processing — is safe. [9](#0-8) 

---

### Proof of Concept

1. Alice calls `depositCollateral("default", USDC, 5e6)` (≥ $5). `isValidDepositAmount` passes because `subaccountIds[alice_default] == 0` and `5e6 >= MIN_FIRST_DEPOSIT_AMOUNT`. Funds are transferred; a slow-mode tx is enqueued. `subaccountIds[alice_default]` is still `0`.

2. One hour later, Alice calls `depositCollateral("default", USDC, 1e5)` ($0.10). `isValidDepositAmount` is called again. `subaccountIds[alice_default]` is still `0` (slow-mode tx not yet processed). The function selects `MIN_FIRST_DEPOSIT_AMOUNT = $5`. The `require` reverts with `ERR_DEPOSIT_TOO_SMALL`.

3. Alice is forced to deposit at least $5 again, even though she already has $5 pending credit. This continues for up to 3 days until the sequencer processes the first slow-mode transaction and `_recordSubaccount` is finally called. [1](#0-0) [10](#0-9)

### Citations

**File:** core/contracts/Endpoint.sol (L90-101)
```text
    function isValidDepositAmount(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount
    ) internal returns (bool) {
        int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;
        if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
            minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;
        }
        return
            clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
    }
```

**File:** core/contracts/Endpoint.sol (L111-114)
```text
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
```

**File:** core/contracts/Endpoint.sol (L137-142)
```text
        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }
```

**File:** core/contracts/Endpoint.sol (L152-166)
```text
        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/common/Constants.sol (L40-42)
```text
int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```

**File:** core/contracts/EndpointTx.sol (L209-216)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
```

**File:** core/contracts/EndpointStorage.sol (L67-72)
```text
    function _recordSubaccount(bytes32 subaccount) internal {
        if (subaccountIds[subaccount] == 0) {
            subaccountIds[subaccount] = ++numSubaccounts;
            subaccounts[numSubaccounts] = subaccount;
        }
    }
```
