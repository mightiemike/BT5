### Title
Deposited ERC20 Tokens Permanently Stuck in Endpoint When Slow-Mode `DepositCollateral` Transaction Fails — (`core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` transfers ERC20 tokens from the caller into the `Endpoint` contract **before** the corresponding slow-mode `DepositCollateral` transaction is executed. If that slow-mode transaction fails during execution, the tokens are permanently irrecoverable: the catch block in `_executeSlowModeTransaction` is intentionally empty, as confirmed by the inline comment `// try return funds now removed`.

---

### Finding Description

**Step 1 — Tokens are taken at submission time.**

`depositCollateralWithReferral` calls `handleDepositTransfer` to pull ERC20 tokens from `msg.sender` into the `Endpoint` contract, then enqueues a slow-mode `DepositCollateral` transaction. The tokens are now held by `Endpoint`. [1](#0-0) 

**Step 2 — Execution failure is silently swallowed.**

When the slow-mode transaction is later executed via `_executeSlowModeTransaction`, `processSlowModeTransaction` is called inside a `try/catch`. If it reverts, the catch block does nothing. The comment `// try return funds now removed` (line 226) explicitly documents that a prior refund mechanism was deleted, leaving no recovery path. [2](#0-1) 

**Step 3 — The slow-mode tx is deleted before execution.**

The queue entry is deleted (`delete slowModeTxs[_slowModeConfig.txUpTo++]`) before the `try` call, so even if the failure is detected externally, the transaction cannot be retried. [3](#0-2) 

**Step 4 — Concrete failure conditions in `processSlowModeTransactionImpl`.**

For a `DepositCollateral` slow-mode tx, execution calls `_recordSubaccount` and then `clearinghouse.depositCollateral`. Either can revert:

- `_recordSubaccount` may revert if the subaccount state is inconsistent.
- `clearinghouse.depositCollateral` calls `spotEngine.updateBalance`; if the spot engine is upgraded or the product state changes between the 3-day submission window and execution, this can revert. [4](#0-3) [5](#0-4) 

**Step 5 — `DirectDepositV1` amplifies the risk.**

`DirectDepositV1.creditDeposit()` calls `depositCollateralWithReferral` on behalf of a fixed `subaccount`. If the resulting slow-mode tx fails, the tokens are stuck in `Endpoint`. The DDA's own `withdraw` function only drains the DDA contract's balance, not the `Endpoint` contract's balance, so there is no owner-accessible recovery path either. [6](#0-5) 

---

### Impact Explanation

Any ERC20 tokens transferred into `Endpoint` via `depositCollateralWithReferral` (or `depositCollateral`) whose corresponding slow-mode transaction subsequently fails are **permanently locked** in the `Endpoint` contract. There is no admin rescue function, no retry mechanism, and no refund path. The user's subaccount balance is never credited, and the on-chain tokens are unrecoverable. This is a direct asset loss for the depositing user or the DDA contract owner.

---

### Likelihood Explanation

The 3-day `SLOW_MODE_TX_DELAY` window between submission and execution creates a meaningful gap during which protocol state can change. Realistic triggers include:

- A spot engine upgrade between submission and execution that alters `updateBalance` behavior.
- A product configuration change that causes `_decimals` to revert (e.g., token address zeroed out).
- Any revert inside `_recordSubaccount` for a subaccount that was valid at submission but invalid at execution.

The comment `// try return funds now removed` is direct evidence that the developers were aware of this failure mode and deliberately removed the safeguard, making the vulnerability latent but structurally present.

---

### Recommendation

Restore a refund path in the catch block of `_executeSlowModeTransaction`. For slow-mode transactions of type `DepositCollateral`, the catch block should identify the original depositor and the token/amount from the stored `SlowModeTx` data and return the tokens. Alternatively, store a mapping of `(txIdx → (depositor, token, amount))` at submission time so the catch block can issue a refund without re-parsing the transaction bytes.

---

### Proof of Concept

1. User calls `Endpoint.depositCollateral(subaccountName, productId, amount)`.
2. `depositCollateralWithReferral` is invoked: `handleDepositTransfer` pulls `amount` tokens from the user into `Endpoint`. A slow-mode tx is enqueued with `executableAt = block.timestamp + 3 days`.
3. During the 3-day window, the spot engine is upgraded and `updateBalance` for `productId` now reverts under certain conditions.
4. After 3 days, anyone calls `executeSlowModeTransaction()`. The slow-mode tx is deleted from the queue. `processSlowModeTransaction` is called inside `try`; `clearinghouse.depositCollateral` → `spotEngine.updateBalance` reverts.
5. The catch block executes: `// try return funds now removed` — nothing happens.
6. The user's `amount` tokens remain in `Endpoint` forever. The user's subaccount balance was never credited. No function exists to recover the tokens. [7](#0-6) [8](#0-7)

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

**File:** core/contracts/DirectDepositV1.sol (L83-100)
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
```
