### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users simultaneously opens the pool to every unpermissioned user who routes through the same router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool sees `msg.sender = router`: [3](#0-2) 

The effective allowlist lookup therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The router carries no information about the originating user to the extension; `extensionData` is opaque bytes forwarded as-is and the extension does not decode a user identity from it.

This creates an irreconcilable bind for any pool admin who deploys `SwapAllowlistExtension`:

| Admin intent | What they must configure | Actual result |
|---|---|---|
| Allow specific users via router | `allowedSwapper[pool][router] = true` | **Every** user can bypass the allowlist through the router |
| Block non-allowlisted users from router | Leave router un-allowlisted | Allowlisted users cannot use the router at all (DoS) |

There is no configuration that simultaneously (a) allows allowlisted users to use the router and (b) blocks non-allowlisted users from using the router.

The `DepositAllowlistExtension` has the same structural pattern for `beforeAddLiquidity`, where `sender` is the direct caller of `pool.addLiquidity()` — the `MetricOmmPoolLiquidityAdder` when used — rather than the economic owner of the position. [4](#0-3) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted protocols) is fully bypassed by any unpermissioned user who routes through `MetricOmmSimpleRouter`. The attacker receives the same execution quality as an allowlisted user. This breaks the core access-control invariant of the pool and constitutes a direct policy bypass with fund-impacting consequences: non-allowlisted users can drain liquidity from a pool that was designed to serve only a restricted set of counterparties.

**Severity: High** — the bypass is unconditional once the router is allowlisted, requires no privileged access, and is reachable through the standard supported periphery path.

### Likelihood Explanation

**Medium** — the scenario requires the pool admin to allowlist the router address (a natural and expected configuration step for any pool that wants its curated users to access the router). The `IMetricOmmSimpleRouter` interface documentation explicitly states that `tokenIn / tokenOut` against pool immutables "remain the caller's obligation off-chain," indicating the router is a first-class supported entry point. Any production pool that enables router access for its allowlisted users is immediately vulnerable.

### Recommendation

The extension must resolve the true economic actor, not the intermediary. Two complementary approaches:

1. **Pass originating user through `extensionData`**: The router encodes `msg.sender` (the originating user) into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. This requires a coordinated encoding convention between router and extension.

2. **Check `sender` only when it is not a registered router**: `BaseMetricExtension` (or the factory) maintains a registry of approved routers. If `sender` is a registered router, the extension falls back to checking the payer address recovered from `extensionData`; otherwise it checks `sender` directly.

The simplest safe fix is option 1: the router always appends `abi.encode(msg.sender)` to `extensionData` for each hop, and the extension decodes it as the authoritative identity to gate.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router for allowlisted users
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended curated user
  - bob is NOT allowlisted

Attack:
  1. bob calls router.exactInputSingle({pool: pool, tokenIn: token0, zeroForOne: true, ...})
  2. Router calls pool.swap(recipient=bob, zeroForOne=true, ..., extensionData="")
  3. Pool calls extension.beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  → passes
  5. Swap executes; bob receives token1 output

Result: bob, a non-allowlisted address, successfully swaps on a curated pool.
Direct pool call by bob (without router) would revert with NotAllowedToSwap.
``` [2](#0-1) [5](#0-4) [1](#0-0)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L1-42)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmExtensions} from "@metric-core/interfaces/extensions/IMetricOmmExtensions.sol";
import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";
import {IDepositAllowlistExtension} from "../interfaces/extensions/IDepositAllowlistExtension.sol";
import {BaseMetricExtension} from "./base/BaseMetricExtension.sol";

/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }

  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
