### Title
Silent Deposit Failure Without Revert in `depositCollateralWithReferral` Allows Successful Transactions With No State Change — (File: `core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.depositCollateralWithReferral` silently returns without reverting when the deposit amount fails the minimum-deposit validation. An unprivileged caller invoking this `public` function directly with a sub-minimum amount receives a successful on-chain transaction, but no tokens are transferred and no deposit is queued. This is a direct analog to the Compound error-propagation pattern: the protocol swallows the failure condition instead of reverting, violating the "Fail Early and Loudly" principle.

---

### Finding Description

`depositCollateralWithReferral` is declared `public` and is the canonical deposit entry point for both direct user calls and the `DirectDepositV1` integration path. [1](#0-0) 

When `isValidDepositAmount` returns `false` (amount below `MIN_DEPOSIT_AMOUNT` or `MIN_FIRST_DEPOSIT_AMOUNT`), the function executes a bare `return` at line 141 — no revert, no event, no error code. The token transfer and slow-mode queue insertion that follow at lines 144–166 are never reached.

The sibling entry point `depositCollateral` does guard with a `require`: [2](#0-1) 

But because `depositCollateralWithReferral` is `public`, any caller — including an integrating contract, a wallet, or a user — can bypass `depositCollateral` and call `depositCollateralWithReferral` directly. The silent-return path is then fully reachable with no privilege requirement.

The design comment at line 138–140 acknowledges the intentional suppression of the revert:

> "we cannot revert here, otherwise direct deposit could be blocked when there are multiple assets awaiting credit but one of them is below the minimum deposit amount."

This rationale applies to the `DirectDepositV1` batch flow, but it is applied unconditionally to all callers of the `public` function, including direct user calls.

---

### Impact Explanation

A user or integrating contract calling `depositCollateralWithReferral` directly with a sub-minimum amount receives a transaction receipt with `status: 1` (success). No tokens leave the caller's wallet, and no `DepositCollateral` slow-mode transaction is enqueued. The subaccount balance is never updated. If the caller (e.g., a wallet UI or aggregator) interprets the success receipt as confirmation that the deposit was processed, it may display a false "deposit confirmed" message and allow the user to proceed to place orders against a zero balance — orders that will subsequently fail health checks.

The corrupted state delta is: **expected `subaccount.balance += amount`; actual `subaccount.balance` unchanged**, with no on-chain signal of failure.

---

### Likelihood Explanation

Any caller that invokes `depositCollateralWithReferral` directly — rather than through `depositCollateral` — is exposed. This includes:
- Integrating contracts (e.g., routers, aggregators) that call the `public` function directly.
- Users whose deposit amount falls below the minimum due to price movement between quote time and execution time (since `isValidDepositAmount` calls `clearinghouse.checkMinDeposit` which uses a live oracle price).
- The `DirectDepositV1` contract, which is explicitly designed to call this path and silently skip assets.

The oracle-price dependency means a deposit that was valid at signing time can silently fail at execution time with no on-chain indication.

---

### Recommendation

1. Split the function into two variants: a `public` one that reverts on invalid amounts (for direct user calls), and an `internal` one that silently skips (for the `DirectDepositV1` batch path).
2. Alternatively, emit a `DepositSkipped(subaccount, productId, amount)` event when the silent-return path is taken, so off-chain tooling can detect and surface the failure.
3. Document clearly that `depositCollateralWithReferral` does not guarantee a deposit occurred, and that callers must not infer success from a non-reverting call.

---

### Proof of Concept

1. Alice calls `Endpoint.depositCollateralWithReferral(aliceSubaccount, productId, 1 /* wei */, "")` directly.
2. `isValidDepositAmount` returns `false` (amount below minimum).
3. Function executes `return` at line 141 — no revert, no transfer, no slow-mode entry.
4. Transaction receipt: `status: 1`.
5. Alice's subaccount balance: unchanged.
6. Alice's wallet UI shows "Deposit Successful."
7. Alice places an order; it fails with `ERR_SUBACCT_HEALTH` because her balance is zero. [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/Endpoint.sol (L103-120)
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
```

**File:** core/contracts/Endpoint.sol (L123-142)
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
