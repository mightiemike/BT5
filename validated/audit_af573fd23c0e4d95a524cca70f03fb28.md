Looking at the Nado codebase, I need to find an analog to the external report's vulnerability class: **unhandled/silently swallowed exceptions in queued message processing that cause asset loss or queue blockage**.

The key pattern in the external report:
1. A message receiver calls into another contract
2. That contract can revert
3. The revert is not handled, causing permanent failure

Let me trace the Nado slow-mode queue execution path carefully.

---

### Title
Removed Refund Mechanism in `_executeSlowModeTransaction` Causes Permanent Loss of Pre-Transferred Deposit Collateral on Silent Failure — (`File: core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint._executeSlowModeTransaction` uses a `try/catch` that silently discards all failures from `processSlowModeTransaction`. A comment in the catch block explicitly reads `// try return funds now removed`, confirming a refund mechanism was deliberately removed. For `DepositCollateral` slow-mode transactions, ERC-20 tokens are transferred from the user to the `Endpoint` contract **before** the slow-mode entry is queued. If the slow-mode transaction later fails during execution, the tokens are permanently locked in the `Endpoint` contract with no recovery path.

---

### Finding Description

**Step 1 — Tokens transferred before slow-mode entry is queued.**

`depositCollateralWithReferral` calls `handleDepositTransfer` (pulling tokens from the caller into the `Endpoint`) and then appends a `DepositCollateral` entry to the slow-mode queue: [1](#0-0) 

**Step 2 — Slow-mode entry is deleted from the queue before execution is attempted.**

`_executeSlowModeTransaction` increments `txUpTo` and deletes the entry from storage **before** the `try` block runs: [2](#0-1) 

**Step 3 — Exceptions are silently swallowed; the refund path was removed.**

The catch block contains only an out-of-gas heuristic. The comment `// try return funds now removed` is direct evidence that a refund mechanism existed and was deliberately deleted: [3](#0-2) 

**Step 4 — `clearinghouse.depositCollateral` has revert conditions not pre-screened by `checkMinDeposit`.**

`checkMinDeposit` (called by `isValidDepositAmount` before the token transfer) validates `amount <= INT128_MAX` and `decimals <= MAX_DECIMALS`. However, `depositCollateral` additionally calls `spotEngine.updateBalance`, whose revert conditions are not replicated in `checkMinDeposit`: [4](#0-3) 

If `spotEngine.updateBalance` reverts for any reason (e.g., arithmetic overflow on `int128(txn.amount) * int128(multiplier)` for a token whose decimal configuration changes between deposit and execution, or any future engine-level guard), the catch block silently discards the failure. The tokens already transferred to `Endpoint` are permanently unrecoverable.

**Step 5 — `DepositCollateral` cannot be re-submitted via `submitSlowModeTransaction`.**

`submitSlowModeTransactionImpl` explicitly reverts on `DepositCollateral` type, so there is no alternative path for the user to re-credit their deposit: [5](#0-4) 

---

### Impact Explanation

Any user who calls `depositCollateralWithReferral` (or `depositCollateral`) and whose resulting slow-mode transaction fails silently permanently loses the deposited ERC-20 tokens. The tokens sit in the `Endpoint` contract with no on-chain mechanism to retrieve them. The corrupted state is the user's token balance: tokens are debited from the user's wallet but never credited to their protocol subaccount, and the slow-mode entry is already deleted from the queue.

---

### Likelihood Explanation

The 3-day `SLOW_MODE_TX_DELAY` window between deposit and execution creates a meaningful exposure period. Any state divergence between `checkMinDepin` (evaluated at deposit time) and `depositCollateral` (evaluated at execution time) is sufficient to trigger the loss. Concrete triggers include: a token whose decimal count is altered by the token contract itself between deposit and execution (non-standard ERC-20 tokens with mutable decimals), or any future addition of a revert guard inside `spotEngine.updateBalance`. The removed refund comment confirms this was a known design gap. Likelihood is low-to-medium given the 3-day window and the existence of non-standard tokens.

---

### Recommendation

Restore a refund mechanism inside the catch block of `_executeSlowModeTransaction`. When a `DepositCollateral` slow-mode transaction fails, the contract should transfer the deposited tokens back to the original depositor (`txn.sender`). Alternatively, re-add the pre-execution validation that mirrors all revert conditions of `clearinghouse.depositCollateral` so that the token transfer is only performed when execution is guaranteed to succeed.

---

### Proof of Concept

1. User calls `Endpoint.depositCollateralWithReferral(subaccount, productId, amount, referral)`.
2. `handleDepositTransfer` pulls `amount` tokens from the user into `Endpoint`. [6](#0-5) 
3. A `SlowModeTx` entry is appended to `slowModeTxs[txCount]`. [7](#0-6) 
4. After 3 days, anyone calls `executeSlowModeTransaction()`.
5. `_executeSlowModeTransaction` deletes the entry (`delete slowModeTxs[txUpTo++]`) and enters the `try` block. [8](#0-7) 
6. `processSlowModeTransaction` → `clearinghouse.depositCollateral` → `spotEngine.updateBalance` reverts.
7. The `catch` block fires. The out-of-gas heuristic does not trigger. Execution continues silently. The comment `// try return funds now removed` confirms no refund is issued. [9](#0-8) 
8. The user's tokens remain in `Endpoint` permanently. The slow-mode entry is gone. No re-submission path exists for `DepositCollateral` type. [5](#0-4)

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

**File:** core/contracts/EndpointTx.sol (L343-344)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
```
