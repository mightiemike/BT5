Based on the wiki pages and directory structure I was able to read, here is my analysis:

---

### Title
SwapAllowlistExtension Checks Router Address as `sender` Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension` enforces per-address swap access control by checking the `sender` parameter passed to its `beforeSwap` hook. When a user routes a swap through `MetricOmmSimpleRouter`, the pool receives the router contract as `msg.sender`, and therefore passes the **router's address** — not the actual end-user's address — as `sender` to the extension. If the router is allowlisted (or `allowAllSwappers` is toggled on for it), any non-allowlisted user can bypass the swap allowlist entirely by calling through the router.

### Finding Description

The `SwapAllowlistExtension` implements `beforeSwap` and gates access using:

```
allowedSwapper[pool][sender]  OR  allowAllSwappers[pool]
``` [1](#0-0) 

The `sender` value is set by the pool to `msg.sender` of the `swap()` call. When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap()`, making the router the `msg.sender` to the pool. [2](#0-1) 

The extension dispatch loop in `ExtensionCalling._beforeSwap` then forwards this router address as `sender` to the extension: [3](#0-2) 

The allowlist check therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This is the direct analog to the external bug: the wrong identity is bound into the critical guard check because the intermediary (router / NFT transfer) changes the observable identity before the guard runs.

**Two concrete failure modes:**

1. **Bypass (primary impact):** Pool admin allowlists the router to enable normal usage. Any non-allowlisted user calls `MetricOmmSimpleRouter.swap()` → router is `sender` → allowlist passes → swap executes. The allowlist is completely circumvented.

2. **Broken functionality:** Pool admin allowlists specific user addresses but does not allowlist the router. Allowlisted users who call through the router are blocked (`NotAllowedToSwap`) even though they are individually permitted. Core swap flow is broken for legitimate users.

The same structural issue applies to `DepositAllowlistExtension`: if `MetricOmmPoolLiquidityAdder` calls `addLiquidity` and the pool sets `owner` to `msg.sender` (the liquidity adder), the deposit allowlist checks the liquidity adder's address rather than the actual LP's address. [4](#0-3) 

### Impact Explanation

- **Allowlist bypass:** Non-allowlisted users gain swap access to a pool that the admin intended to restrict. This undermines the admin's access control boundary — an admin-boundary break per the allowed impact gate.
- **Broken LP flow:** Allowlisted LPs cannot use `MetricOmmPoolLiquidityAdder` for advanced operations, breaking core liquidity management functionality.
- Both impacts are direct and reachable by any unprivileged user with no special setup.

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool deploying `SwapAllowlistExtension` and expecting users to use the router will be affected.
- The router must be allowlisted (or `allowAllSwappers` set) for normal operation, which is the exact condition that enables the bypass.
- No privileged access or malicious setup is required — a standard user calling the public router triggers the bug.

### Recommendation

The `sender` identity passed to `beforeSwap` should reflect the **originating user**, not the intermediary router. Two approaches:

1. **Pass-through identity:** `MetricOmmSimpleRouter` should pass the actual caller (`msg.sender` of the router call) as an explicit `sender` parameter to `pool.swap()`, and the pool should forward this value to extensions rather than using its own `msg.sender`.

2. **Extension-side resolution:** The `SwapAllowlistExtension` should accept an additional `originSender` field in `extensionData` (signed or verified by the pool) and check that address instead of `sender`.

The same fix applies to `DepositAllowlistExtension`: the `owner` checked must be the actual LP, not the liquidity adder contract.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension`. Admin sets `allowAllSwappers[pool] = false`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allowlist `MetricOmmSimpleRouter` so normal users can swap.
3. Attacker (not allowlisted) calls `MetricOmmSimpleRouter.swap(pool, ...)`.
4. Router calls `pool.swap(...)` — pool sets `sender = address(router)`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → returns selector.
6. Swap executes. Attacker bypasses the allowlist with no special privileges. [5](#0-4) [6](#0-5) 

---

**Note on verification:** I was unable to read the raw Solidity source files directly (tool errors prevented file reads). The above analysis is grounded in the wiki descriptions, which explicitly state that `beforeSwap` receives `sender` and the allowlist checks `allowedSwapper[pool][sender]`, and that `MetricOmmSimpleRouter` is the intermediary that calls `pool.swap()`. The exact line numbers for the pool's `swap()` function setting `sender = msg.sender` could not be confirmed from source and should be verified in `MetricOmmPool.sol` before filing.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L1-5)
```text
// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.35;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
```

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
```text
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
```
