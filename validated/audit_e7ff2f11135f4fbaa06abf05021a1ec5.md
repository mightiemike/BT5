### Title
Pre-Existing DDA Balance Inflates Deposit Amount, Bypassing Minimum Deposit Check — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` uses the contract's **total** token balance (`token.balanceOf(address(this))`) as the deposit amount rather than computing the delta (`balanceAfter - balanceBefore`). This is the direct analog of the reported `RewardsLiquidator.vy` bug: a pre-existing balance in the contract inflates the apparent "received" amount, allowing the minimum deposit check to be satisfied by tokens that were not part of the current deposit action.

---

### Finding Description

`creditDeposit()` is a permissionless `external` function that sweeps every supported token balance held by the DDA contract and deposits it into the protocol on behalf of the fixed `subaccount`:

```solidity
// core/contracts/DirectDepositV1.sol L83-101
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        ...
        uint256 balance = token.balanceOf(address(this));   // ← total balance, not delta
        if (balance != 0) {
            token.approve(address(endpoint), balance);
            endpoint.depositCollateralWithReferral(
                subaccount,
                productId,
                uint128(balance),                           // ← passed as deposit amount
                "-1"
            );
        }
    }
}
```

`depositCollateralWithReferral` in `Endpoint.sol` (L123–167) enforces a minimum deposit gate:

```solidity
if (!isValidDepositAmount(subaccount, productId, amount)) {
    return;   // skip without transferring
}
handleDepositTransfer(token, msg.sender, uint256(amount));
```

Because `amount` is the **total** DDA balance (not the newly received delta), any pre-existing balance in the DDA — from a prior failed/skipped deposit, a direct ERC-20 transfer by any party, or accumulated dust — is silently folded into the current deposit amount. This has two concrete consequences:

1. **Minimum deposit check bypass**: If the DDA holds a residual balance of `X` and a new deposit of `Y` arrives where `Y < MIN_DEPOSIT_AMOUNT` but `X + Y >= MIN_DEPOSIT_AMOUNT`, `creditDeposit()` will process the deposit using the combined total, bypassing the per-deposit minimum that `isValidDepositAmount` is meant to enforce.

2. **Permissionless forced deposit (griefing)**: Because `creditDeposit()` has no access control, any caller can invoke it at any time. If the DDA owner has tokens sitting in the DDA that they intend to retrieve via `withdraw()`, an adversary can front-run the `withdraw()` call with `creditDeposit()`, locking those tokens into the protocol's slow-mode queue instead. The owner cannot recover them from the DDA directly; they must go through the protocol's withdrawal flow (which has a `SLOW_MODE_TX_DELAY` of three days per `Endpoint.sol` L153).

---

### Impact Explanation

- **Minimum deposit bypass**: Dust/sub-minimum token amounts can be deposited into a subaccount by piggybacking on a pre-existing DDA balance, circumventing the spam-prevention gate in `isValidDepositAmount`.
- **Forced deposit / griefing**: Any unprivileged caller can permanently redirect tokens sitting in a DDA into the protocol, removing the DDA owner's ability to withdraw them directly and imposing a mandatory three-day withdrawal delay.

The corrupted state is: `subaccount` collateral balance in `SpotEngine` is incremented by the full DDA balance (including pre-existing tokens), and the slow-mode queue receives a `DepositCollateral` entry for the inflated amount.

---

### Likelihood Explanation

- `creditDeposit()` is `external` with no access control — any EOA or contract can call it at any time.
- DDAs are deployed per-user and their addresses are deterministic/public, making them observable on-chain.
- A griefing attacker only needs to monitor DDA balances and call `creditDeposit()` before the owner calls `withdraw()`. No capital is required; only gas.
- The minimum deposit bypass requires only that a DDA accumulates a residual balance above the minimum threshold, which is a normal operational state (e.g., after a prior deposit that was skipped due to being below minimum).

---

### Recommendation

Compute the deposited amount as the balance delta rather than the total balance:

```solidity
uint256 balanceBefore = token.balanceOf(address(this));
// ... (tokens arrive via transfer-in, or use current balance as the sweep)
uint256 balanceAfter = token.balanceOf(address(this));
uint256 delta = balanceAfter - balanceBefore;
```

For the sweep use-case, restrict `creditDeposit()` to `onlyOwner` so that only the DDA owner can trigger the deposit, preventing permissionless forced deposits.

---

### Proof of Concept

1. Alice deploys a `DirectDepositV1` DDA. Her DDA holds a residual 90 USDC from a prior skipped deposit (below `MIN_DEPOSIT_AMOUNT` of 100 USDC).
2. Alice sends 15 USDC to her DDA intending to later call `withdraw()` to recover both amounts.
3. Bob (unprivileged) calls `creditDeposit()` on Alice's DDA.
4. `token.balanceOf(address(this))` returns 105 USDC (90 + 15).
5. `isValidDepositAmount` passes (105 >= 100).
6. `handleDepositTransfer` moves 105 USDC from the DDA to the Clearinghouse.
7. A slow-mode `DepositCollateral` tx is queued for 105 USDC — Alice's 15 USDC is now locked in the protocol for at least three days, and the 90 USDC residual that was below the minimum has been force-deposited against Alice's intent. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L83-101)
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
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/Endpoint.sol (L90-101)
```text
    function isValidDepositAmount(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount
    ) internal returns (bool) {
        int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;
        if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
            minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;
        }
        return
            clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
    }
```

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
