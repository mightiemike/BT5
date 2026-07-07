### Title
Unsafe `uint256`→`uint128` Cast in `creditDeposit()` Silently Truncates Deposit Amount, Corrupting Subaccount Collateral Accounting - (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` reads a token balance as `uint256`, approves the full amount to the endpoint, but passes only `uint128(balance)` to `depositCollateralWithReferral`. If the DDA's token balance exceeds `type(uint128).max`, the cast silently truncates the value. The endpoint transfers and credits only the truncated amount, while the excess tokens remain stranded in the DDA. The subaccount receives less collateral than the tokens actually held, permanently corrupting its collateral accounting.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, the token balance is fetched as `uint256` and then unsafely narrowed to `uint128` when passed to the endpoint:

```solidity
// core/contracts/DirectDepositV1.sol, lines 90–98
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
    token.approve(address(endpoint), balance);          // approves full uint256
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),   // <-- silent truncation if balance > type(uint128).max
        "-1"
    );
}
``` [1](#0-0) 

Inside `Endpoint.depositCollateralWithReferral`, the `amount` parameter (already truncated to `uint128`) is forwarded directly to `handleDepositTransfer` as `uint256(amount)`, meaning only the truncated quantity is pulled from the DDA and recorded in the slow-mode queue:

```solidity
// core/contracts/Endpoint.sol, lines 144–165
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)          // amount is the truncated uint128 value
);
// ...
DepositCollateral({ sender: subaccount, productId: productId, amount: amount })
``` [2](#0-1) 

The slow-mode transaction records `amount` (the truncated value) as the canonical deposit. When the sequencer processes it, the subaccount is credited with only the truncated collateral. The difference between the actual balance and the truncated amount stays in the DDA contract, inaccessible to the subaccount.

No `SafeCast` library is imported or used anywhere in `DirectDepositV1.sol`, and no overflow guard exists before the cast. [3](#0-2) 

---

### Impact Explanation

- **Corrupted collateral accounting**: The subaccount's on-chain balance reflects only `uint128(balance)`, not the true deposited amount. Any health checks, margin calculations, or liquidation thresholds computed against this subaccount will use an understated collateral figure.
- **Stranded tokens**: The overflow portion of the balance (`balance - uint128(balance)`) remains in the DDA. While the DDA owner can recover it via `withdraw()`, the subaccount never receives credit for it, creating a permanent discrepancy between tokens held and collateral credited.
- **No revert, no event**: The truncation is silent. Neither the caller nor the subaccount owner receives any indication that the deposit was understated. [4](#0-3) 

---

### Likelihood Explanation

`creditDeposit()` carries no access-control modifier — any external address can call it at any time. [5](#0-4) 

`type(uint128).max` ≈ 3.4 × 10³⁸ raw token units. For standard 18-decimal tokens this is ~3.4 × 10²⁰ whole tokens, and for 6-decimal tokens (e.g., USDC) it is ~3.4 × 10³² whole tokens — both astronomically large under normal conditions. However:

- The Nado DDA is designed to accumulate arbitrary token balances sent by users before `creditDeposit()` is called. A token with a very high total supply or very low per-unit value could realistically reach this threshold.
- The protocol explicitly supports multiple token types via `spotEngine.getProductIds()`, and future token listings are not bounded by current supply figures.
- Because the function is permissionless, an attacker who can influence the DDA balance (e.g., by donating tokens) and then call `creditDeposit()` at the right moment can trigger the truncation deliberately.

Likelihood is **low-to-medium** given current token supplies, but the absence of any guard makes it a latent, permanently exploitable path.

---

### Recommendation

Replace the bare cast with a checked conversion. Since no `SafeCast` library is currently imported, either:

1. Add OpenZeppelin's `SafeCast` and use `SafeCast.toUint128(balance)`, which reverts on overflow, or
2. Add an explicit inline guard before the cast:

```solidity
require(balance <= type(uint128).max, "balance exceeds uint128");
endpoint.depositCollateralWithReferral(
    subaccount,
    productId,
    uint128(balance),
    "-1"
);
```

The same pattern used elsewhere in the codebase — `require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW)` — should be applied here consistently. [6](#0-5) 

---

### Proof of Concept

1. A DDA is created for `subaccount` via `ContractOwner.createDirectDepositV1(subaccount)`.
2. A supported token with a total supply exceeding `type(uint128).max` raw units is listed on the protocol.
3. Tokens accumulate in the DDA until `token.balanceOf(dda) > type(uint128).max`. Call this value `B`.
4. Any external caller invokes `DirectDepositV1(dda).creditDeposit()`.
5. `token.approve(endpoint, B)` is called — the full `B` is approved.
6. `endpoint.depositCollateralWithReferral(subaccount, productId, uint128(B), "-1")` is called with `uint128(B) = B mod 2^128`, a value far smaller than `B`.
7. `handleDepositTransfer` pulls only `uint128(B)` tokens from the DDA.
8. The slow-mode queue records a `DepositCollateral` transaction for `uint128(B)`.
9. When processed, the subaccount is credited with `uint128(B)` collateral.
10. The remaining `B - uint128(B)` tokens stay in the DDA, unaccounted for in the subaccount's collateral balance. [4](#0-3) [7](#0-6)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L1-12)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";

interface IIERC20Base {
    function transfer(address to, uint256 amount) external returns (bool);

    function balanceOf(address account) external view returns (uint256);

    function approve(address spender, uint256 amount) external returns (bool);
}
```

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

**File:** core/contracts/Clearinghouse.sol (L703-703)
```text
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
```
