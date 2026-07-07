### Title
Permanently Locked Collateral When `DepositCollateral` Slow Mode Transaction Fails Silently — (`File: core/contracts/Endpoint.sol`)

---

### Summary

When a user calls `depositCollateralWithReferral`, tokens are transferred to the `Endpoint` contract before the corresponding `DepositCollateral` slow mode transaction is queued. If that slow mode transaction later fails during execution (e.g., because the product was delisted in the interim), the catch block performs no refund. The slow mode entry is already deleted from the queue, and the deposited tokens are permanently locked in the contract with no recovery path.

---

### Finding Description

`Endpoint.sol::depositCollateralWithReferral` follows a two-step pattern:

1. **Step 1 — Token transfer in** (lines 144–148): `handleDepositTransfer` pulls tokens from `msg.sender` into the `Endpoint` contract.
2. **Step 2 — Queue slow mode tx** (lines 152–166): A `DepositCollateral` slow mode transaction is enqueued with a 3-day delay. [1](#0-0) 

When the slow mode transaction is later executed via `_executeSlowModeTransaction`, the entry is **deleted from the queue first** (line 194), then processed inside a `try/catch`: [2](#0-1) 

If `processSlowModeTransaction` reverts, the catch block does nothing. The comment on line 226 is explicit:

```
// try return funds now removed
```

This confirms a refund path was intentionally removed, leaving no mechanism to recover the tokens. The slow mode entry is already gone (deleted on line 194), so the deposit cannot be retried either.

The processing path for `DepositCollateral` in `EndpointTx.sol::processSlowModeTransactionImpl` calls `clearinghouse.depositCollateral(txn)`, which can revert for several reasons (e.g., `ERR_INVALID_PRODUCT` if the product was delisted, minimum deposit check failure, or any other clearinghouse-level revert): [3](#0-2) 

---

### Impact Explanation

User tokens are permanently locked inside the `Endpoint` contract. There is no emergency withdrawal function, no way to re-queue the failed deposit, and no admin recovery path visible in the codebase. The corrupted state is: `token.balanceOf(address(endpoint)) > 0` while the user's on-chain collateral balance in `SpotEngine` remains zero, with no path to reconcile the two.

---

### Likelihood Explanation

Low-to-medium. The 3-day slow mode delay creates a window during which protocol state can change. The most realistic trigger is a product being delisted (`DelistProduct` slow mode tx) between the time a user deposits and the time their `DepositCollateral` slow mode tx executes. Both are valid slow mode transactions that any user or admin can submit. A user depositing into a product that is subsequently delisted before their slow mode tx executes will lose their funds permanently.

---

### Recommendation

Restore the fund-return logic in the catch block of `_executeSlowModeTransaction`. When a `DepositCollateral` slow mode transaction fails, the contract should decode the transaction, identify the token and amount, and refund the depositor. Alternatively, maintain a mapping of pending deposits keyed by slow mode index so that users can self-recover funds from failed slow mode deposit transactions.

---

### Proof of Concept

```
1. Alice calls depositCollateralWithReferral(subaccount, productId=5, amount=1000e6).
   → handleDepositTransfer pulls 1000 USDC from Alice into Endpoint.
   → SlowModeTx queued at index N, executableAt = now + 3 days.

2. Admin submits a DelistProduct slow mode tx for productId=5.
   → Sequencer executes it before Alice's deposit slow mode tx.
   → productToEngine[5] is now cleared.

3. After 3 days, anyone calls executeSlowModeTransaction() for index N.
   → delete slowModeTxs[N] — entry is gone.
   → try this.processSlowModeTransaction(...) → clearinghouse.depositCollateral(txn)
     → reverts with ERR_INVALID_PRODUCT (product 5 no longer registered).
   → catch block: // try return funds now removed — no refund issued.

4. Alice's 1000 USDC is permanently locked in Endpoint.
   Alice's SpotEngine balance for productId=5 = 0.
   No function exists to recover the funds.
``` [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/Endpoint.sol (L123-167)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

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
    }
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
