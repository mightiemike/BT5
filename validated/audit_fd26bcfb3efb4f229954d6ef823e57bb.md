### Title
Slow Mode Transaction Failure Permanently Locks User Deposited Collateral — (`core/contracts/Endpoint.sol`)

---

### Summary

When a slow mode transaction fails during execution in `_executeSlowModeTransaction`, the `catch` block silently discards the failure with no refund to the user. For `DepositCollateral` transactions queued via `depositCollateralWithReferral`, the user's ERC-20 tokens are transferred to the `Clearinghouse` **before** the slow mode TX is executed. If `clearinghouse.depositCollateral` reverts during execution, those tokens are permanently locked in the `Clearinghouse` with no credit to the user's account. The codebase itself acknowledges this with the comment `// try return funds now removed` at the exact failure-handling site.

---

### Finding Description

**Step 1 — Upfront token transfer in `depositCollateralWithReferral`**

`Endpoint.depositCollateralWithReferral` calls `handleDepositTransfer`, which immediately moves the user's ERC-20 tokens from `msg.sender` to `address(clearinghouse)`: [1](#0-0) 

The slow mode TX is then queued with no fee charged and no on-chain credit yet issued to the user: [2](#0-1) 

**Step 2 — Deferred execution in `_executeSlowModeTransaction`**

Up to 3 days later, `_executeSlowModeTransaction` attempts to execute the queued TX via an external `try/catch`: [3](#0-2) 

**Step 3 — Failure silently discarded, refund code removed**

If `processSlowModeTransaction` reverts (e.g., because the product was delisted, the subaccount was sanctioned between deposit and execution, or any other validation in `clearinghouse.depositCollateral` fails), the `catch` block runs. The comment at line 226 is the explicit admission:

```
// try return funds now removed
```

No token transfer back to the user is performed. The tokens remain in the `Clearinghouse` with no accounting entry crediting the user's subaccount. [4](#0-3) 

**Step 4 — `processSlowModeTransactionImpl` for non-deposit slow mode TXs**

For all other slow mode transaction types (e.g., `WithdrawCollateral`, `LinkSigner`), `submitSlowModeTransactionImpl` charges a `SLOW_MODE_FEE` of $1 USDC upfront via `chargeSlowModeFee` before queuing: [5](#0-4) 

`SLOW_MODE_FEE` is defined as `1000000` ($1 in 6-decimal USDC): [6](#0-5) 

If the slow mode TX fails during execution, this fee is also not refunded.

---

### Impact Explanation

For the `DepositCollateral` path: the user's full deposited collateral amount (potentially large, e.g., thousands of USDC) is transferred to the `Clearinghouse` before the slow mode TX is executed. If execution fails, the tokens are permanently locked — the `Clearinghouse` holds them but the user's subaccount balance is never incremented. There is no recovery path in the current code.

For other slow mode TX types: the $1 USDC slow mode fee is non-refundable on failure, a smaller but still concrete loss.

---

### Likelihood Explanation

The `depositCollateralWithReferral` path is callable by any unprivileged user. Failure conditions for `clearinghouse.depositCollateral` include: the product being delisted between deposit and execution (3-day window), the subaccount being sanctioned in that window, or any future validation added to `depositCollateral`. The 3-day `SLOW_MODE_TX_DELAY` creates a meaningful window for state changes that could cause execution to fail. [7](#0-6) 

---

### Recommendation

Restore the fund-return logic in the `catch` block of `_executeSlowModeTransaction`. For `DepositCollateral` failures, transfer the tokens back from the `Clearinghouse` to the original depositor. For other slow mode TX types, refund the `SLOW_MODE_FEE` to the sender. The `SlowModeTx` struct already stores `sender` and the transaction payload, which contains the depositor address and amount needed to compute the refund. [8](#0-7) 

---

### Proof of Concept

1. User calls `depositCollateralWithReferral(subaccount, productId, 10_000e6)` — 10,000 USDC is transferred to `Clearinghouse`. A `SlowModeTx` is queued.
2. Within the 3-day window, the product `productId` is delisted via a sequencer `DelistProduct` transaction.
3. After 3 days, anyone calls `executeSlowModeTransaction()`. `processSlowModeTransaction` calls `clearinghouse.depositCollateral`, which reverts because the product is delisted.
4. The `catch` block at line 207 fires. Line 226 (`// try return funds now removed`) is reached. No refund is issued.
5. The user's 10,000 USDC is permanently locked in the `Clearinghouse` with no subaccount credit. [9](#0-8)

### Citations

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

**File:** core/contracts/Endpoint.sol (L185-229)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
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
        }
    }
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```
