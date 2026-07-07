### Title
Blacklisted `sendTo` Address in `submitWithdrawal` Causes Irreversible Sequencer Batch Revert — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.handleWithdrawTransfer` performs a direct `token.safeTransfer(to, amount)` to the withdrawal recipient with no fallback. If `to` is blacklisted by a token such as USDC, the transfer reverts. Because `Endpoint.submitTransactionsChecked` has no per-transaction error handling, a single blocked withdrawal causes the entire sequencer batch to revert, halting all protocol operations.

---

### Finding Description

In `BaseWithdrawPool.submitWithdrawal`, when the clearinghouse processes a withdrawal, it unconditionally calls `handleWithdrawTransfer`, which performs a direct push transfer to `sendTo`: [1](#0-0) 

```solidity
function handleWithdrawTransfer(
    IERC20Base token,
    address to,
    uint128 amount
) internal virtual {
    token.safeTransfer(to, uint256(amount));
}
```

If `to` is on the USDC blacklist, `safeTransfer` reverts. This revert propagates unhandled through the full call stack:

`BaseWithdrawPool.submitWithdrawal` → `Clearinghouse.handleWithdrawTransfer` → `Clearinghouse.withdrawCollateral` → `EndpointTx.processTransactionImpl` → `Endpoint._delegatecallEndpointTx` → `Endpoint.processTransaction` → `Endpoint.submitTransactionsChecked` [2](#0-1) 

The `_delegatecallEndpointTx` wrapper re-raises any revert from the delegatecall unconditionally: [3](#0-2) 

The sequencer's primary batch submission path has no per-transaction try/catch: [4](#0-3) 

```solidity
for (uint256 i = 0; i < transactions.length; i++) {
    bytes calldata transaction = transactions[i];
    processTransaction(transaction);
    nSubmissions += 1;
}
```

A single failing withdrawal reverts the entire batch. Because `nSubmissions` is not advanced, the sequencer cannot move forward without manually excluding the stuck transaction.

Contrast this with the slow-mode path, which does have a try/catch: [5](#0-4) 

No equivalent protection exists for the fast sequencer path.

Additionally, `WithdrawCollateralV2` allows a user-specified `sendTo` address: [6](#0-5) 

Because `handleWithdrawTransfer` executes **before** the balance deduction in `withdrawCollateral`: [7](#0-6) 

a revert leaves the user's on-chain balance unchanged. Since the nonce increment inside `validateNonce` is also reverted, the attacker can resubmit the same transaction indefinitely at zero cost, creating a persistent griefing vector.

---

### Impact Explanation

A single withdrawal targeting a USDC-blacklisted address causes `submitTransactionsChecked` to revert entirely. `nSubmissions` does not advance, so the sequencer cannot process any subsequent transaction — no trades, no liquidations, no other withdrawals — until the stuck transaction is manually identified and excluded off-chain. With `WithdrawCollateralV2`, an attacker can deliberately set `sendTo` to a known blacklisted address and repeat the attack at no cost (balance and nonce are both reverted on failure), making the halt persistent.

---

### Likelihood Explanation

USDC blacklisting is a real, actively used mechanism (OFAC compliance). Any user whose address is sanctioned after submitting a withdrawal triggers this condition inadvertently. The `WithdrawCollateralV2` griefing path requires only a valid subaccount and knowledge of any publicly blacklisted USDC address, both of which are trivially obtainable.

---

### Recommendation

Implement a pull pattern for all token withdrawals in `BaseWithdrawPool`. Instead of immediately transferring tokens to `sendTo` inside `submitWithdrawal`, record the pending amount in a per-address claimable balance mapping and let users call a separate `claim()` function. This decouples sequencer batch processing from the liveness of any individual recipient address, directly mirroring the fix recommended in the referenced Moloch report. [8](#0-7) 

---

### Proof of Concept

1. User A holds USDC collateral and submits a `WithdrawCollateral` (or `WithdrawCollateralV2` with `sendTo = knownBlacklistedAddress`) transaction signed with their key.
2. User A's address (or the specified `sendTo`) is added to the USDC blacklist.
3. Sequencer includes the withdrawal in a batch and calls `submitTransactionsChecked`.
4. Execution reaches `BaseWithdrawPool.handleWithdrawTransfer` → `token.safeTransfer(blacklistedAddress, amount)` → **REVERT**.
5. The revert propagates through `_delegatecallEndpointTx` back to `submitTransactionsChecked`, reverting the entire batch.
6. `nSubmissions` is not incremented; the sequencer cannot submit any new batch with a valid `idx`.
7. For the `WithdrawCollateralV2` griefing variant: since both the nonce increment and balance deduction are reverted, the attacker resubmits the identical transaction in the next batch, repeating the halt indefinitely at zero cost.

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

**File:** core/contracts/Clearinghouse.sol (L408-413)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);
```

**File:** core/contracts/Endpoint.sol (L68-84)
```text
    function _delegatecallEndpointTx(bytes memory callData)
        internal
        returns (bytes memory)
    {
        require(endpointTx != address(0), "Endpoint Tx not set");
        (bool success, bytes memory result) = endpointTx.delegatecall(callData);
        if (!success) {
            if (result.length == 0) {
                revert();
            }
            // solhint-disable-next-line no-inline-assembly
            assembly {
                revert(add(result, 0x20), mload(result))
            }
        }
        return result;
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

**File:** core/contracts/Endpoint.sol (L289-293)
```text
        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
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
