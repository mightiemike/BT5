### Title
Deposited Collateral Permanently Locked When Slow Mode `DepositCollateral` Transaction Fails During Execution — (File: `core/contracts/Endpoint.sol`)

---

### Summary

In `depositCollateralWithReferral`, ERC20 tokens are transferred from the user to the `Endpoint` contract **before** the corresponding `DepositCollateral` slow mode transaction is enqueued. If that slow mode transaction later fails during execution, the `catch` block silently discards the failure with no refund. A code comment at the catch site explicitly reads `// try return funds now removed`, confirming a prior refund mechanism was deliberately deleted. The slow mode entry is also deleted from the queue before the try, so the transaction cannot be retried. The deposited tokens are permanently locked in the `Endpoint` contract.

---

### Finding Description

`depositCollateralWithReferral` (Endpoint.sol lines 123–167) executes in two distinct, non-atomic steps:

**Step 1 — Token custody (line 144):**
```solidity
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)
);
```
Tokens are pulled from `msg.sender` into the `Endpoint` contract immediately.

**Step 2 — Slow mode enqueue (lines 152–165):**
A `SlowModeTx` of type `DepositCollateral` is appended to the queue with a hardcoded 3-day execution delay (`SLOW_MODE_TX_DELAY`). [1](#0-0) 

When `_executeSlowModeTransaction` is later called, it **first deletes the entry** from the queue (line 194), then calls `processSlowModeTransaction` inside a `try/catch`:

```solidity
SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
delete slowModeTxs[_slowModeConfig.txUpTo++];   // entry gone before try
...
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed          ← explicit acknowledgment
}
``` [2](#0-1) 

If `processSlowModeTransaction` reverts for any reason, the catch block does nothing. The slow mode entry is already deleted (line 194), so it cannot be retried. The tokens transferred in Step 1 remain in the `Endpoint` contract with no recovery path.

Inside `processSlowModeTransactionImpl`, the `DepositCollateral` branch calls `clearinghouse.depositCollateral(txn)`: [3](#0-2) 

`depositCollateral` in `Clearinghouse.sol` contains multiple revert conditions that can be triggered by state changes occurring during the 3-day delay window:

```solidity
require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
uint8 decimals = _decimals(txn.productId);
require(decimals <= MAX_DECIMALS);
``` [4](#0-3) 

`_decimals` itself reverts with `ERR_INVALID_PRODUCT` if the product's token address is zero:

```solidity
function _decimals(uint32 productId) internal virtual returns (uint8) {
    IERC20Base token = IERC20Base(_tokenAddress(productId));
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    return token.decimals();
}
``` [5](#0-4) 

Any of these reverts — product delisted during the 3-day window, `spotEngine.updateBalance` reverting due to an internal invariant, or any future code path added to `depositCollateral` — will silently discard the failure and leave the user's tokens permanently locked.

---

### Impact Explanation

A user's full deposited collateral (real ERC20 tokens already transferred to the `Endpoint` contract) is permanently frozen. There is no `rescue`, `emergencyWithdraw`, or admin recovery function visible in `Endpoint.sol`. The slow mode queue entry is deleted before the try, so the deposit cannot be replayed. The impact is **permanent, irrecoverable loss of deposited funds** for the affected user.

---

### Likelihood Explanation

The 3-day hardcoded `SLOW_MODE_TX_DELAY` creates a meaningful window during which protocol state can change. Product delisting (`delistProduct`) is an owner-callable slow mode transaction that can be submitted and executed within the same 3-day window as a pending deposit. Additionally, any internal revert in `spotEngine.updateBalance` (e.g., due to a future invariant check, an upgrade, or an edge case in balance arithmetic) would silently trigger this path. The explicit comment `// try return funds now removed` confirms the team is aware of this gap and chose not to address it, making the risk persistent across all future code changes.

---

### Recommendation

Restore a refund mechanism in the `catch` block of `_executeSlowModeTransaction`. When a `DepositCollateral` slow mode transaction fails, the contract should identify the original depositor from the stored `SlowModeTx` data and return the deposited tokens:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // Restore: refund deposited tokens to original sender on DepositCollateral failure
    _tryRefundDeposit(txn);
}
```

Alternatively, restructure `depositCollateralWithReferral` to only transfer tokens at execution time (inside `processSlowModeTransactionImpl`), not at submission time, eliminating the custody gap entirely.

---

### Proof of Concept

1. User calls `depositCollateralWithReferral(subaccount, productId, amount, referral)` on `Endpoint`.
2. `handleDepositTransfer` transfers `amount` tokens from the user to the `Endpoint` contract (line 144). Tokens are now in contract custody.
3. A `SlowModeTx` of type `DepositCollateral` is enqueued with `executableAt = block.timestamp + 3 days` (line 153).
4. During the 3-day window, the owner submits and executes a `DelistProduct` slow mode transaction for `productId`. The product's token address is zeroed out in the spot engine config.
5. After 3 days, anyone calls `executeSlowModeTransaction()` (or the sequencer includes `ExecuteSlowMode`).
6. `_executeSlowModeTransaction` deletes the slow mode entry (line 194) and calls `processSlowModeTransaction` in a try/catch.
7. Inside `processSlowModeTransactionImpl`, `clearinghouse.depositCollateral(txn)` calls `_decimals(productId)`, which calls `_tokenAddress(productId)` returning `address(0)`, triggering `require(address(token) != address(0), ERR_INVALID_PRODUCT)` — revert.
8. The catch block executes: gas check passes, comment `// try return funds now removed` — no refund issued.
9. The slow mode entry is already deleted. The user's tokens remain in the `Endpoint` contract with no recovery path.

### Citations

**File:** core/contracts/Endpoint.sol (L144-166)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

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

**File:** core/contracts/Endpoint.sol (L193-227)
```text
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
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

**File:** core/contracts/Clearinghouse.sol (L187-191)
```text
    function _decimals(uint32 productId) internal virtual returns (uint8) {
        IERC20Base token = IERC20Base(_tokenAddress(productId));
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        return token.decimals();
    }
```

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```
