### Title
Collateral Permanently Stuck in Clearinghouse Due to Token Blacklist With No Emergency Recovery — (`core/contracts/BaseWithdrawPool.sol`)

---

### Summary

The Nado withdrawal pipeline terminates with an unconditional `token.safeTransfer(to, amount)` in `BaseWithdrawPool.handleWithdrawTransfer()`. If the collateral token implements a blacklist and the recipient address is blacklisted post-deposit, every withdrawal attempt reverts. Because `Clearinghouse.withdrawCollateral()` calls `handleWithdrawTransfer()` **before** decrementing the user's engine balance, the revert leaves the user's accounting balance intact while the actual tokens remain permanently locked in the Clearinghouse. Neither `Clearinghouse.sol` nor `BaseWithdrawPool.sol` contains an emergency rescue function.

---

### Finding Description

**Withdrawal call chain:**

```
EndpointTx.processTransactionImpl()          [EndpointTx.sol:413-436 / 437-465]
  └─ Clearinghouse.withdrawCollateral()       [Clearinghouse.sol:391-421]
       └─ Clearinghouse.handleWithdrawTransfer()  [Clearinghouse.sol:377-385]
            ├─ token.safeTransfer(withdrawPool, amount)
            └─ BaseWithdrawPool.submitWithdrawal() [BaseWithdrawPool.sol:116-132]
                 └─ BaseWithdrawPool.handleWithdrawTransfer() [BaseWithdrawPool.sol:184-190]
                      └─ token.safeTransfer(to, amount)  ← REVERTS if `to` is blacklisted
```

`Clearinghouse.withdrawCollateral()` calls `handleWithdrawTransfer()` on line 408 **before** it calls `spotEngine.updateBalance()` on line 412: [1](#0-0) 

This ordering means that when `token.safeTransfer(to, amount)` reverts inside `BaseWithdrawPool.handleWithdrawTransfer()`, the entire call unwinds — the user's SpotEngine balance is **never decremented** and the tokens remain in the Clearinghouse. The user's accounting state is intact but the tokens are irrecoverable. [2](#0-1) 

The `WithdrawCollateralV2` path in `EndpointTx.processTransactionImpl()` passes a caller-supplied `sendTo` address directly to `Clearinghouse.withdrawCollateral()`: [3](#0-2) 

If that `sendTo` address is blacklisted, the same permanent lock applies.

**Slow-mode path:** `Endpoint._executeSlowModeTransaction()` wraps `processSlowModeTransaction` in a try/catch that silently drops failures. The comment `// try return funds now removed` confirms a prior refund mechanism was deleted: [4](#0-3) 

For slow-mode withdrawals the transaction is consumed from the queue, the transfer never executes, and the user cannot resubmit because the nonce has already advanced.

**No emergency rescue exists.** `BaseWithdrawPool.removeLiquidity()` routes through the same `handleWithdrawTransfer()` and would fail identically: [5](#0-4) 

`Clearinghouse.sol` has no owner-callable function to transfer tokens to an alternative address. [6](#0-5) 

---

### Impact Explanation

A user whose collateral token address is blacklisted post-deposit loses permanent access to their deposited funds. The tokens sit in `Clearinghouse.sol` with no on-chain path to recover them. The user's SpotEngine balance remains positive (the accounting record is intact), but no withdrawal — sequencer-submitted or slow-mode — can succeed. The asset delta is the full deposited collateral balance for the affected subaccount.

---

### Likelihood Explanation

Moderate. The protocol accepts multiple ERC-20 collateral tokens. Widely used tokens such as USDC and USDT implement operator-controlled blacklists. A user can be blacklisted post-deposit due to regulatory action or compliance flags entirely outside the protocol's control. The `WithdrawCollateralV2` path additionally allows specifying an arbitrary `sendTo` address, widening the surface to any address that could be blacklisted at the time of withdrawal processing.

---

### Recommendation

Add an emergency collateral rescue function in `Clearinghouse.sol`, callable only by the owner (ideally a multisig with timelock), that:
1. Accepts a `subaccount`, `productId`, `amount`, and an alternative `rescueTo` address.
2. Transfers tokens directly to `rescueTo` without routing through `BaseWithdrawPool`.
3. Decrements the subaccount's SpotEngine balance by the rescued amount to preserve accounting integrity.

This mirrors the recommendation in the reference report and avoids the blacklist-induced revert by allowing the owner to redirect funds to a non-blacklisted address.

---

### Proof of Concept

1. User calls `Endpoint.depositCollateral(subaccountName, productId, amount)` with a USDC-collateral product. Tokens flow: User → Endpoint → Clearinghouse. SpotEngine balance credited. [7](#0-6) 

2. The token operator blacklists the user's address.

3. Sequencer includes a `WithdrawCollateral` transaction in a batch. `EndpointTx.processTransactionImpl()` calls:
   ```
   clearinghouse.withdrawCollateral(sender, productId, amount, address(0), nSubmissions)
   ```
   `sendTo` resolves to `address(uint160(bytes20(sender)))` — the blacklisted user address. [8](#0-7) 

4. Execution reaches `BaseWithdrawPool.handleWithdrawTransfer()`:
   ```solidity
   token.safeTransfer(to, uint256(amount)); // to = blacklisted address → REVERT
   ``` [9](#0-8) 

5. The entire call reverts. `spotEngine.updateBalance()` is never reached. Tokens remain in Clearinghouse. SpotEngine balance unchanged.

6. No function in `Clearinghouse.sol` or `BaseWithdrawPool.sol` can rescue the tokens. Funds are permanently locked.

### Citations

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L404-413)
```text
        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);
```

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/Endpoint.sol (L103-121)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
    }
```

**File:** core/contracts/Endpoint.sol (L205-228)
```text
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
```
