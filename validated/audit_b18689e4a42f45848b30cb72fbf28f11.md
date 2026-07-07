### Title
Fee-on-Transfer Token Deposit Overcredits User Subaccount, Creating Protocol Solvency Deficit — (File: `core/contracts/Endpoint.sol`)

---

### Summary

When a user deposits a fee-on-transfer (FOT) token via `depositCollateral`, the protocol pulls `amount` from the user (receiving only `amount − fee` due to the token's transfer tax) but queues a slow-mode `DepositCollateral` transaction that credits the full user-specified `amount` to the subaccount. The protocol's internal accounting is permanently inflated relative to its actual token holdings, creating a solvency deficit exploitable by any depositor.

---

### Finding Description

In `Endpoint.sol`, `depositCollateral` validates the deposit size against `MIN_DEPOSIT_AMOUNT` using the user-supplied `amount`, then calls `depositCollateralWithReferral`, which:

1. Calls `handleDepositTransfer(token, msg.sender, uint256(amount))` — pulling `amount` raw tokens from the caller. If the token charges a fee on transfer, the contract receives only `amount − fee`.
2. Immediately queues a slow-mode transaction encoding `DepositCollateral({ sender: subaccount, productId: productId, amount: amount })` — using the original user-specified `amount`, not the actual received amount. [1](#0-0) 

When the sequencer later processes this slow-mode transaction, `spotEngine.updateBalance` is called with the full `amount`, crediting the user's subaccount as if the protocol received the full `amount`. The protocol holds `amount − fee` tokens but has credited `amount` worth of balance — a permanent per-deposit deficit equal to the transfer fee.

The minimum deposit check at line 111–113 also uses the raw `amount` before the transfer, so the actual credited amount (`amount − fee`) is never validated against `MIN_DEPOSIT_AMOUNT`. [2](#0-1) 

---

### Impact Explanation

**Solvency/accounting corruption.** The sum of all subaccount balances for a FOT product token exceeds the protocol's actual token holdings by the cumulative transfer fees across all deposits. A user who deposits and then withdraws the full credited amount forces the protocol to transfer more tokens than it received. Repeated across multiple depositors, this drains the protocol's reserves for that product, ultimately causing legitimate withdrawals to fail or be underfunded. The `assertUtilization` check in `withdrawCollateral` may not catch this until the pool is already insolvent. [3](#0-2) 

---

### Likelihood Explanation

**Medium.** The trigger requires a fee-on-transfer token to be listed as a supported spot product. If any such token is listed (e.g., tokens with deflationary mechanics or protocol-level transfer taxes), every deposit by any unprivileged user silently inflates the accounting. No special privileges, sequencer compromise, or social engineering are required — `depositCollateral` is a public, permissionless entry point callable by any address. [4](#0-3) 

---

### Recommendation

Replace the use of the user-supplied `amount` in the queued slow-mode transaction with the **actual received amount**, measured via a balance-before/balance-after pattern inside `handleDepositTransfer`:

```solidity
uint256 balanceBefore = token.balanceOf(address(this));
token.safeTransferFrom(from, address(this), amount);
uint256 actualReceived = token.balanceOf(address(this)) - balanceBefore;
```

Use `actualReceived` (cast to `uint128`) as the `amount` field in the `DepositCollateral` slow-mode transaction, and re-validate it against `MIN_DEPOSIT_AMOUNT` after the transfer. This mirrors the mitigation recommended in the Slingshot report: measure the real post-transfer balance delta rather than trusting the nominal transfer amount.

---

### Proof of Concept

1. A FOT token with a 1% transfer fee is listed as a supported spot product (`productId = X`).
2. Attacker calls `depositCollateral(subaccountName, X, 1000e18)`.
3. `handleDepositTransfer` executes `safeTransferFrom(attacker, endpoint, 1000e18)`. Due to the 1% fee, the contract receives `990e18`.
4. The slow-mode transaction is queued with `amount = 1000e18`.
5. The sequencer processes the slow-mode transaction; `spotEngine.updateBalance` credits the attacker's subaccount with `1000e18` worth of balance.
6. Attacker calls `WithdrawCollateral` for `1000e18`. The protocol transfers `1000e18` out but only holds `990e18` from this deposit — a `10e18` deficit per deposit cycle.
7. Repeated across many depositors, the protocol becomes insolvent for this product. [5](#0-4) [6](#0-5)

### Citations

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
