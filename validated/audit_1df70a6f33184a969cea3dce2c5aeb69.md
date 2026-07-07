### Title
Unsafe `uint256` to `uint128` Downcast in `DirectDepositV1#creditDeposit()` Causes Subaccount Under-Crediting — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

In `DirectDepositV1.creditDeposit()`, the contract's full ERC-20 token balance (`uint256`) is unsafely truncated to `uint128` before being passed to `endpoint.depositCollateralWithReferral()`. When the balance exceeds `type(uint128).max`, the truncated value is deposited and credited to the subaccount, while the excess tokens remain silently stranded in the DDA contract.

---

### Finding Description

`DirectDepositV1.creditDeposit()` reads the contract's token balance as a `uint256`, approves the full amount to the endpoint, then passes `uint128(balance)` as the deposit amount: [1](#0-0) 

```solidity
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
    token.approve(address(endpoint), balance);
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),   // <-- unsafe downcast
        "-1"
    );
}
```

`Endpoint.depositCollateralWithReferral()` accepts a `uint128 amount` parameter and uses it for both the token pull and the slow-mode deposit record: [2](#0-1) 

The endpoint pulls exactly `uint256(amount)` = `uint256(uint128(balance))` tokens from the DDA and enqueues a `DepositCollateral` slow-mode transaction crediting the subaccount with the same truncated `amount`. No overflow check or upper-bound guard exists anywhere in this path.

---

### Impact Explanation

When `balance > type(uint128).max` (≈ 3.4 × 10³⁸):

- `uint128(balance)` silently wraps. For example, if `balance = type(uint128).max + 1`, then `uint128(balance) = 0`.
- The endpoint pulls 0 (or a much smaller truncated value) from the DDA.
- The subaccount is credited with 0 (or the truncated value) instead of the full balance.
- The excess tokens remain stranded in the DDA contract. While the DDA owner can recover them via `withdraw()`, the intended subaccount never receives the correct credit, breaking the deposit accounting invariant. [3](#0-2) 

---

### Likelihood Explanation

`type(uint128).max` is approximately 3.4 × 10³⁸. For tokens with 18 decimals this represents ~3.4 × 10²⁰ whole tokens; for 6-decimal tokens (e.g. USDC) ~3.4 × 10³² whole tokens. In normal operation this threshold is practically unreachable. However, the DDA accumulates balances passively from any sender before `creditDeposit()` is called, and no input validation prevents a token with unusual decimals or a deliberately inflated balance from triggering the truncation. The likelihood is low but non-zero, matching the Medium severity assigned to the analogous Concur finding.

---

### Recommendation

Add an explicit upper-bound guard before the downcast in `creditDeposit()`:

```solidity
require(balance <= type(uint128).max, "Balance exceeds uint128 max");
```

Or use a safe-cast library (e.g. OpenZeppelin's `SafeCast.toUint128(balance)`) which reverts on overflow instead of silently truncating. [1](#0-0) 

---

### Proof of Concept

Assume a token with 18 decimals accumulates a balance of `type(uint128).max + 1` in the DDA (e.g. via a large airdrop or aggregated micro-deposits).

1. `creditDeposit()` is called by any external actor.
2. `balance = type(uint128).max + 1` (a valid `uint256`).
3. `token.approve(address(endpoint), balance)` — approves the full amount.
4. `endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1")` — `uint128(balance) = 0`.
5. Inside `depositCollateralWithReferral`, `isValidDepositAmount` returns `false` for amount `0`, so the function returns early without pulling any tokens or enqueuing a deposit.
6. **Result**: The subaccount is credited with `0`. All `type(uint128).max + 1` tokens remain in the DDA. The depositor's funds are not credited to the protocol subaccount. [4](#0-3)

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
