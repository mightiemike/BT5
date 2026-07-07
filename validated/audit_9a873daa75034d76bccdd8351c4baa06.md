### Title
Silent Failure in `_executeSlowModeTransaction` Permanently Locks Deposited Collateral in Clearinghouse — (`core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` transfers user tokens into the `Clearinghouse` contract **before** queuing a `DepositCollateral` slow-mode transaction to credit the balance. If that slow-mode transaction later fails during execution, `_executeSlowModeTransaction` silently swallows the failure via a `try/catch` with no fund-return path. The tokens remain in `Clearinghouse` with no corresponding balance entry, effectively locking them. The removed comment `// try return funds now removed` confirms this recovery path was intentionally deleted.

---

### Finding Description

`depositCollateralWithReferral` in `Endpoint.sol` performs two sequential steps:

1. **Immediate token transfer** — `handleDepositTransfer` moves tokens from the caller through `Endpoint` into `Clearinghouse`.
2. **Deferred balance credit** — a `DepositCollateral` slow-mode transaction is enqueued; it is executed later (by the sequencer or by anyone after a 3-day delay) to credit the subaccount balance. [1](#0-0) 

When the slow-mode transaction is eventually executed, `_executeSlowModeTransaction` wraps the call in a `try/catch`:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed
}
``` [2](#0-1) 

If `processSlowModeTransaction` reverts for any reason other than out-of-gas, the catch block executes, the slow-mode entry is already deleted from the queue (`delete slowModeTxs[_slowModeConfig.txUpTo++]` runs unconditionally before the `try`), and **no funds are returned**. The tokens sit in `Clearinghouse` with no balance credited and no queue entry remaining to retry. [3](#0-2) 

---

### Impact Explanation

A user's deposited collateral becomes permanently inaccessible inside `Clearinghouse`. The subaccount balance is never credited, so the user cannot trade, withdraw, or recover the tokens through any normal protocol path. The asset delta is: tokens leave the user's wallet and enter `Clearinghouse`, but no corresponding balance entry is ever written. The only recovery would require an owner-level intervention (e.g., `withdrawFromDirectDepositV1` or a contract upgrade), which is not a user-accessible path. [4](#0-3) 

---

### Likelihood Explanation

Concrete failure triggers for `processSlowModeTransaction` on a `DepositCollateral` entry include:

- **Sanctioned address**: `depositCollateralWithReferral` checks `requireUnsanctioned` at submission time, but if the subaccount is added to the sanctions list in the 3-day window before execution, the execution-time check inside `processSlowModeTransactionImpl` will revert.
- **Product delisted**: if the product is delisted between deposit and execution, `clearinghouse.depositCollateral` will revert.
- **`DirectDepositV1.creditDeposit` path**: this function is callable by anyone with no access control, loops over all product IDs, and calls `depositCollateralWithReferral` for each token balance. A single product-level failure in the slow-mode execution phase locks all tokens deposited for that product. [5](#0-4) 

The 3-day slow-mode delay (`SLOW_MODE_TX_DELAY`) materially increases the window during which state can change and cause execution failure.

---

### Recommendation

Restore the fund-return path in the `catch` block of `_executeSlowModeTransaction`. When a `DepositCollateral` slow-mode transaction fails, the contract should detect the transaction type and transfer the tokens back to the original depositor, mirroring the previously removed logic. Alternatively, do not transfer tokens into `Clearinghouse` at submission time; instead, hold them in `Endpoint` and only forward them upon successful execution of the slow-mode transaction (analogous to using `ReplyOn::Always` in the CosmWasm context).

---

### Proof of Concept

1. User calls `depositCollateralWithReferral(subaccount, productId, amount, ref)`.
2. `handleDepositTransfer` moves `amount` tokens: `user → Endpoint → Clearinghouse`. [6](#0-5) 
3. A `DepositCollateral` slow-mode tx is enqueued with `executableAt = block.timestamp + 3 days`. [7](#0-6) 
4. Before execution, the subaccount is added to the sanctions list (or the product is delisted).
5. After 3 days, anyone calls `executeSlowModeTransaction()`. `_executeSlowModeTransaction` deletes the queue entry, then calls `processSlowModeTransaction` inside `try`. The call reverts due to the sanctions/delist check.
6. The `catch` block runs. `gasleft()` is well above the threshold, so `invalid()` is not triggered. The comment `// try return funds now removed` marks the absent recovery path. The function returns normally.
7. `amount` tokens remain in `Clearinghouse`. The subaccount balance is `0`. The queue entry is gone. The user has no recourse through any unprivileged entrypoint. [2](#0-1)

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

**File:** core/contracts/Endpoint.sol (L193-194)
```text
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];
```

**File:** core/contracts/Endpoint.sol (L207-227)
```text
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

**File:** core/contracts/ContractOwner.sol (L622-647)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
    {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
        } else {
            uint256 preBalance = IERC20Base(token).balanceOf(address(this));
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(token));
            uint256 postBalance = IERC20Base(token).balanceOf(address(this));
            require(postBalance > preBalance, "empty");
            IERC20Base(token).safeTransfer(
                msg.sender,
                postBalance - preBalance
            );
        }
    }
```

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```
