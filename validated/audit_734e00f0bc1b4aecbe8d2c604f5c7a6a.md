### Title
Unsafe `uint256`-to-`uint128` Downcast in `creditDeposit()` Silently Truncates Deposit Amount — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` reads the contract's ERC20 token balance as `uint256` and casts it directly to `uint128` without a bounds check before passing it to `endpoint.depositCollateralWithReferral()`. If the balance exceeds `type(uint128).max`, the cast silently truncates the value, causing the subaccount to be credited for a fraction of the actual tokens held by the DDA contract.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`:

```solidity
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
    token.approve(address(endpoint), balance);
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),   // <-- unsafe downcast, no bounds check
        "-1"
    );
}
```

`balance` is a `uint256` returned by `token.balanceOf()`. It is cast directly to `uint128` with no overflow guard. If `balance > type(uint128).max` (~3.4 × 10^38), the truncated value is passed to `depositCollateralWithReferral`, which then calls `handleDepositTransfer` with only `uint256(uint128(balance))` — the truncated amount — pulling far fewer tokens from the DDA than are actually held there.

The full `uint256` approval is granted to the endpoint, but only the truncated `uint128` amount is pulled. The excess tokens remain in the DDA contract, unaccounted for in the subaccount's balance.

Contrast this with the protected pattern in `ContractOwner._isDepositAmountReady()`, which explicitly guards the same cast:

```solidity
if (balance > INT128_MAX) {
    return true;
}
return oraclePriceX18.mul(int128(uint128(balance))) >= ...
```

No such guard exists in `creditDeposit()`. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

If the DDA's token balance exceeds `type(uint128).max`, the subaccount is credited for only `uint128(balance)` — a silently wrapped, much smaller value — while the actual token surplus remains stranded in the DDA. The subaccount's on-chain balance is corrupted: it reflects far less collateral than was deposited. This directly corrupts the health and collateral accounting for the subaccount, potentially preventing valid withdrawals or trades that should be permitted given the true deposited amount.

The excess tokens are not permanently lost (the owner can recover them via `withdraw()`), but the subaccount's credited balance is permanently understated until a corrective re-deposit is made. [3](#0-2) [4](#0-3) 

---

### Likelihood Explanation

`creditDeposit()` is an unrestricted `external` function — any caller can trigger it. The overflow condition requires a token balance exceeding `type(uint128).max` (~3.4 × 10^38 raw units). For 18-decimal tokens this is ~3.4 × 10^20 tokens, and for 6-decimal tokens (e.g., USDC) ~3.4 × 10^32 units — both astronomically large for any real asset. Likelihood is therefore **low** under normal conditions. However, the absence of any guard is a latent code-level defect: the same class of bug was silently present in XykCurve until explicitly patched, and the correct pattern (bounds check before cast) is already applied elsewhere in this codebase. [3](#0-2) 

---

### Recommendation

Add an explicit overflow check before the cast, consistent with the pattern already used in `ContractOwner._isDepositAmountReady()`:

```solidity
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
+   require(balance <= type(uint128).max, "balance overflow");
    token.approve(address(endpoint), balance);
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),
        "-1"
    );
}
```

Alternatively, use OpenZeppelin's `SafeCast.toUint128(balance)` which reverts on overflow. [1](#0-0) 

---

### Proof of Concept

```solidity
// Demonstrates silent truncation — no revert
contract TestOverflow {
    constructor() {
        uint256 balance = uint256(type(uint128).max) + 1; // e.g. 2^128
        uint128 truncated = uint128(balance);
        // truncated == 0, not 2^128
        // depositCollateralWithReferral is called with amount == 0
        // while the DDA holds 2^128 raw token units
    }
}
```

An attacker or any caller invoking `creditDeposit()` when the DDA holds a balance just above `type(uint128).max` would cause the subaccount to receive zero (or a wrapped small value) credit, while the full token balance remains in the DDA — a silent accounting corruption with no on-chain revert. [1](#0-0)

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

**File:** core/contracts/ContractOwner.sol (L596-601)
```text
        if (balance > INT128_MAX) {
            return true;
        }
        return
            oraclePriceX18.mul(int128(uint128(balance))) >=
            (isFirstDeposit ? MIN_FIRST_DEPOSIT_AMOUNT : MIN_DEPOSIT_AMOUNT);
```

**File:** core/contracts/Endpoint.sol (L144-148)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```
