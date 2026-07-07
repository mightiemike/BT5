### Title
Deposited Collateral Permanently Locked When Slow-Mode `DepositCollateral` Transaction Fails — (`core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` in `Endpoint.sol` transfers user tokens to the clearinghouse in phase 1, then queues a slow-mode `DepositCollateral` transaction for phase 2 (subaccount credit). If phase 2 reverts when executed, the catch block silently discards the failure — an explicit code comment confirms the refund path was deliberately removed. The user's tokens are permanently locked in the clearinghouse with no subaccount credit and no recovery path.

---

### Finding Description

The deposit flow in `depositCollateralWithReferral` is a two-phase, non-atomic operation:

**Phase 1 — immediate token custody** (`Endpoint.sol` lines 144–148):

```solidity
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)
);
```

`handleDepositTransfer` (`EndpointStorage.sol` lines 111–119) pulls tokens from `msg.sender` and forwards them to the clearinghouse immediately.

**Phase 2 — queued subaccount credit** (`Endpoint.sol` lines 152–165):

```solidity
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // 3 days
    sender: sender,
    tx: abi.encodePacked(uint8(TransactionType.DepositCollateral), ...)
});
```

The slow-mode transaction is executed up to 3 days later via `_executeSlowModeTransaction` (`Endpoint.sol` lines 205–227):

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed
}
```

The comment `// try return funds now removed` at line 226 is a direct admission that a refund path previously existed and was removed. If `processSlowModeTransaction` reverts for any non-OOG reason, the catch block silently discards the failure. The tokens already transferred to the clearinghouse in phase 1 are never credited to the subaccount and cannot be recovered.

**Concrete revert triggers in `clearinghouse.depositCollateral` (`Clearinghouse.sol` lines 193–208):**

1. **Product delisted in the 3-day window**: `_decimals(txn.productId)` calls `_tokenAddress(productId)` → `spotEngine.getConfig(productId).token`. If the product is delisted between deposit and execution, the token address is zero and `require(address(token) != address(0), ERR_INVALID_PRODUCT)` reverts.

2. **`amount > INT128_MAX`**: `depositCollateralWithReferral` accepts any `uint128` amount but never checks `amount <= INT128_MAX`. `clearinghouse.depositCollateral` enforces `require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW)` at line 199. A user depositing `amount` in the range `[2^127, 2^128 - 1]` will have tokens taken but the slow-mode tx will always revert.

3. **`spotEngine.updateBalance` revert**: Any revert inside the spot engine during balance update (e.g., utilization assertion, overflow) causes the same silent discard.

---

### Impact Explanation

User tokens are transferred to the clearinghouse in phase 1 and permanently locked there. The subaccount balance is never credited. There is no on-chain mechanism for the user to recover the funds — no refund function, no retry path, and no admin rescue path for this specific scenario. The tokens accumulate as unaccounted surplus in the clearinghouse.

**Corrupted state delta**: `clearinghouse` token balance increases by `amount`, but `spotEngine` balance for `txn.sender` / `txn.productId` is never updated. The user loses the full deposited amount.

---

### Likelihood Explanation

- The 3-day slow-mode delay (`SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60`) creates a realistic window for a product to be delisted via a sequencer-submitted `DelistProduct` slow-mode transaction.
- The `amount > INT128_MAX` path is reachable by any user calling `depositCollateralWithReferral` directly (it is `public`) with a large `uint128` value — no special privilege required.
- The explicit comment `// try return funds now removed` confirms the protocol team is aware of this failure mode and chose not to handle it.

---

### Recommendation

1. Restore a refund path in the `catch` block of `_executeSlowModeTransaction`: when a `DepositCollateral` slow-mode transaction fails, transfer the deposited tokens back to the original sender.
2. Add `require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW)` to `depositCollateralWithReferral` before calling `handleDepositTransfer`, so the overflow case is caught before tokens are taken.
3. Consider making the deposit atomic (transfer tokens only after the subaccount credit succeeds) or storing the depositor address in the slow-mode tx record to enable refunds.

---

### Proof of Concept

1. A product (e.g., `productId = 1`) is active. Alice calls `depositCollateralWithReferral(aliceSubaccount, 1, 1000e6, "-1")`.
2. `handleDepositTransfer` pulls `1000e6` tokens from Alice and sends them to the clearinghouse. A slow-mode `DepositCollateral` tx is queued with `executableAt = now + 3 days`.
3. Within 3 days, the sequencer submits a `DelistProduct` slow-mode transaction for `productId = 1`, which is executed first (sequencer controls ordering).
4. After 3 days, anyone calls `executeSlowModeTransaction()`. `_executeSlowModeTransaction` calls `this.processSlowModeTransaction(alice, depositTx)`.
5. Inside `processSlowModeTransactionImpl`, `clearinghouse.depositCollateral(txn)` is called. `_decimals(1)` calls `spotEngine.getConfig(1).token` → returns `address(0)` → `require(address(token) != address(0), ERR_INVALID_PRODUCT)` reverts.
6. The `catch` block at line 226 discards the revert (`// try return funds now removed`). Alice's `1000e6` tokens remain in the clearinghouse. Her subaccount balance is never credited. No recovery path exists.

---

**Files:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** core/contracts/Endpoint.sol (L205-227)
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
