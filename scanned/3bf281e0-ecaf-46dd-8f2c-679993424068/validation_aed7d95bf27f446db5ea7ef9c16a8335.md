### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper when `MetricOmmSimpleRouter` is used — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the real user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router — a natural step to enable router-mediated swaps for their curated users — every unprivileged address can bypass the per-user gate by routing through the router.

---

### Finding Description

**Call chain that exposes the wrong actor:**

```
user → MetricOmmSimpleRouter.exactInputSingle(pool, ...)
         └─ pool.swap(recipient, ...)          // msg.sender = router
               └─ _beforeSwap(sender=router, ...)
                     └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                           └─ allowedSwapper[pool][router]  ← checked, NOT the real user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router when the user entered through the periphery: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no user-identity forwarding: [4](#0-3) 

The same identity substitution occurs in `exactInput`, `exactOutputSingle`, and `exactOutput` — every router entry point calls `pool.swap` as `msg.sender = router`. [5](#0-4) 

**The invariant broken:** The contest's own audit pivot states: *"swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router."* The extension checks the router's identity, not the real swapper's identity.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, compliance-restricted, or private LP pool) and uses `SwapAllowlistExtension` to restrict swaps to specific addresses faces a binary choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all (broken UX for multi-hop or slippage-protected swaps).
- **Allowlist the router** → every address on-chain can bypass the per-user gate by calling `router.exactInputSingle(pool, ...)`.

In the second case, any unprivileged user can trade against the pool's LP funds at oracle prices, defeating the curation entirely. This is a direct loss of curation policy and potentially of LP principal if the pool was designed to trade only with trusted counterparties.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router. This is a natural and expected action: the router is the canonical periphery entry point, and an admin who wants their allowlisted users to benefit from multi-hop routing or slippage protection will allowlist it. The `SwapAllowlistExtension` gives no indication that allowlisting the router opens the gate to all users. The bypass is then reachable by any unprivileged address with zero additional preconditions.

---

### Recommendation

The extension must resolve the real user identity rather than accepting the `sender` argument at face value when the caller is a known periphery contract. Two viable approaches:

1. **Pass real user identity through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling `pool.swap`. The extension decodes and verifies it. This requires a coordinated change to the router and extension.

2. **Gate on `recipient` instead of `sender` for router paths, or require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` only enforces correctly on direct `pool.swap` calls, and that the router must not be allowlisted.

The cleanest fix is approach 1: the router should encode the originating user address into `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when `sender` is a known router.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only Alice is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so Alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: Charlie (not allowlisted) bypasses the gate via the router
vm.startPrank(charlie);
token0.approve(address(router), type(uint256).max);
// This succeeds: extension sees sender=router, allowedSwapper[pool][router]=true
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: charlie,
    deadline: block.timestamp,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: type(uint128).max,
    extensionData: ""
}));
// Charlie successfully swapped on a pool he was never allowlisted for
```

The extension check `allowedSwapper[pool][router]` passes because the router is allowlisted, even though `charlie` is not. The real user identity is never consulted. [6](#0-5) [7](#0-6)

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
