### Title
Irrecoverable Token Lock When Slow-Mode `DepositCollateral` Fails Silently — (`core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` transfers user tokens to the `Clearinghouse` **before** queuing the slow-mode transaction. If the queued `DepositCollateral` transaction later fails during execution, the tokens are already held by the `Clearinghouse` but the user's subaccount is never credited. The error is swallowed silently and — as confirmed by a code comment — the previously existing refund path was deliberately removed, leaving no recovery mechanism.

---

### Finding Description

`depositCollateralWithReferral` in `Endpoint.sol` performs two sequential operations:

1. **Token transfer** — `handleDepositTransfer` pulls tokens from the caller and forwards them to the `Clearinghouse` immediately.
2. **Queue** — a `DepositCollateral` slow-mode transaction is enqueued with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` (3 days). [1](#0-0) 

`handleDepositTransfer` in `EndpointStorage.sol` performs `safeTransferFrom(token, from, amount)` followed by `safeTransfer(token, address(clearinghouse), amount)` — the funds leave the user's wallet and enter the `Clearinghouse` atomically at deposit time, not at execution time. [2](#0-1) 

When the slow-mode transaction is eventually executed via `_executeSlowModeTransaction`, the call to `processSlowModeTransaction` is wrapped in a `try/catch` that silently discards any revert: [3](#0-2) 

The comment on line 226 is the critical evidence:

```
// try return funds now removed
```

This confirms that a refund path existed and was explicitly removed. No replacement mechanism was added.

Inside `processSlowModeTransactionImpl`, the `DepositCollateral` branch calls `clearinghouse.depositCollateral(txn)` after `_recordSubaccount`. If `clearinghouse.depositCollateral` reverts — for example because the product was delisted in the 3-day window via a `DelistProduct` slow-mode transaction — the catch block in `_executeSlowModeTransaction` absorbs the revert, the slow-mode entry is deleted (`delete slowModeTxs[_slowModeConfig.txUpTo++]`), and the user's tokens remain in the `Clearinghouse` with no subaccount credit and no refund. [4](#0-3) 

---

### Impact Explanation

User tokens are permanently locked inside the `Clearinghouse` contract. The user's subaccount balance is never updated, so the funds are neither accessible for trading nor returnable. There is no on-chain function a user can call to recover them. The only path would be an owner-level intervention (e.g., `WithdrawInsurance` or a contract upgrade), which is not guaranteed and is not a user-accessible recovery mechanism.

---

### Likelihood Explanation

The trigger requires the queued `DepositCollateral` to revert during the 3-day execution window. Concrete realistic triggers:

- **Product delisting**: An admin submits a `DelistProduct` slow-mode transaction that executes before the user's `DepositCollateral`. After delisting, `clearinghouse.depositCollateral` for that `productId` will revert.
- **Sanctions**: The user's address is added to the sanctions list between deposit and execution. `requireUnsanctioned` is checked at submission time but `clearinghouse.depositCollateral` may enforce it again internally.
- **Any other clearinghouse-level revert** introduced by a protocol upgrade during the 3-day window.

The 3-day delay is a meaningful window during which protocol state can change. The `depositCollateralWithReferral` path is also reachable by `DirectDepositV1.creditDeposit()`, which calls it for every product with a non-zero balance, widening the attack surface. [5](#0-4) 

---

### Recommendation

Restore the refund path inside the `catch` block of `_executeSlowModeTransaction`. When a `DepositCollateral` slow-mode transaction fails, the `Clearinghouse` should return the tokens to the original depositor. Concretely:

- Decode the transaction type inside the `catch` block.
- If `txType == DepositCollateral`, call a `Clearinghouse`-level refund function that transfers the credited amount back to `address(bytes20(txn.sender))`.
- Alternatively, defer the token transfer to execution time (i.e., hold tokens in the `Endpoint` during the 3-day window and only forward them to the `Clearinghouse` upon successful execution), which eliminates the race condition entirely.

---

### Proof of Concept

1. User calls `depositCollateralWithReferral` for `productId = P` with `amount = A`. Tokens are immediately transferred to `Clearinghouse`. A slow-mode entry is queued with `executableAt = T + 3 days`.
2. Within 3 days, admin submits and executes a `DelistProduct` slow-mode transaction for `productId = P`.
3. After 3 days, anyone calls `executeSlowModeTransaction`. `_executeSlowModeTransaction` calls `processSlowModeTransaction` which calls `clearinghouse.depositCollateral`. This reverts because `productId = P` is delisted.
4. The `catch` block at line 207 absorbs the revert. The slow-mode entry is deleted at line 194 (`delete slowModeTxs[_slowModeConfig.txUpTo++]`).
5. User's `A` tokens remain in the `Clearinghouse` with no subaccount credit. No refund is issued. The comment `// try return funds now removed` at line 226 confirms no recovery path exists. [6](#0-5)

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

**File:** core/contracts/DirectDepositV1.sol (L83-99)
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
```
