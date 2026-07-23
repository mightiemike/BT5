### Title
SwapAllowlistExtension Checks Router Address Instead of Original Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` of its own `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted — not the original user. If the pool admin adds the router to the allowlist (the only way to let allowlisted users use the router), every unprivileged user can bypass the restriction by routing through the same public contract.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value as the first positional argument to every configured extension: [2](#0-1) 

**Step 2 — The extension checks the forwarded `sender`, not the original EOA.**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the pool's `msg.sender`) and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` inside the extension is the pool: [3](#0-2) 

**Step 3 — The router is `msg.sender` to the pool, not the original user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The pool sees `msg.sender = router`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**Result — The allowlist is keyed on the router address, not the user.**

When any user calls `router.exactInputSingle()`, the extension evaluates `allowedSwapper[pool][router]`. The pool admin faces an impossible choice:

| Admin action | Consequence |
|---|---|
| Do **not** add the router | Allowlisted users cannot use the router at all |
| Add the router | **Every** user can bypass the allowlist via the router |

**Contrast with `DepositAllowlistExtension`**, which correctly gates on the `owner` parameter (the actual position owner), not the caller: [6](#0-5) 

The deposit extension is safe because `addLiquidity` carries `owner` as an explicit argument that the pool passes through unchanged. The swap extension has no equivalent explicit "original swapper" argument — it relies solely on `msg.sender` of the pool call, which is the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted strategies) is completely bypassable by any unprivileged user who calls `MetricOmmSimpleRouter`. The attacker receives pool output tokens at oracle-anchored prices, draining LP value that was intended to be accessible only to allowlisted parties. This is a direct loss of LP principal and a broken core pool functionality (the allowlist guard fails open on the standard periphery path).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the documented, standard user-facing entry point for swaps. Any user who reads the periphery contracts or observes on-chain interactions can discover the bypass. No privileged access, special tokens, or unusual setup is required — a single `exactInputSingle` call suffices. The bypass is reachable on every pool that uses `SwapAllowlistExtension` and has the router allowlisted (or `allowAllSwappers` set to `true`).

---

### Recommendation

Pass the original initiating user through the swap path so the extension can gate on the economically relevant actor. Two approaches:

1. **Add an `originator` field to the swap call or extension payload**: The pool or router records `msg.sender` at the outermost entry point and forwards it as a dedicated argument to `beforeSwap`. The extension checks `allowedSwapper[pool][originator]` instead of `allowedSwapper[pool][sender]`.

2. **Mirror the deposit pattern**: Require callers to supply an explicit `swapper` address (analogous to `owner` in `addLiquidity`). The pool passes it to the extension unchanged. The router sets `swapper = msg.sender` before calling the pool.

Either way, the extension must receive the identity of the user who initiated the trade at the outermost public entry point, not the intermediate contract that relayed the call.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Admin allowlists only `alice` and the router (so alice can use the router).
//    allowedSwapper[pool][alice]  = true
//    allowedSwapper[pool][router] = true   ← required for alice to use the router
// 3. `bob` is NOT allowlisted.

// Attack:
// bob calls router.exactInputSingle({pool: pool, ...})
// router calls pool.swap(recipient, ...)   ← msg.sender = router
// pool calls _beforeSwap(msg.sender=router, ...)
// SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
// Bob's swap executes successfully despite not being allowlisted.

function test_swapAllowlistBypassViaRouter() public {
    // alice and router are allowlisted; bob is not
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // bob bypasses the allowlist through the router
    vm.prank(bob);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: bob,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // bob received token1 despite not being in the allowlist
    assertGt(token1.balanceOf(bob), 0);
}
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
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
  }
```
