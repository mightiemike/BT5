### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap` is called with `msg.sender = router`, so `sender` delivered to the extension is the router address, not the actual user. Any disallowed user can bypass the allowlist by routing through the public router, executing unauthorized swaps against a pool that was configured to restrict access to specific counterparties.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making `msg.sender` of `pool.swap` equal to the router contract: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist status — rather than the actual user's. The same substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The existing unit tests for `SwapAllowlistExtension` only exercise direct pool calls (`vm.prank(address(pool))`), never a router-mediated path, so the identity substitution is untested: [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of counterparties (e.g., KYC'd users, institutional partners). Because the extension sees the router address instead of the real user, two fund-impacting outcomes arise:

1. **Allowlist bypass (critical path):** If the pool admin allowlists the router address (a natural operational choice so that legitimate users can use the official periphery), every user — including explicitly disallowed ones — can swap against the pool by routing through `MetricOmmSimpleRouter`. Unauthorized swaps drain LP token balances at oracle-quoted prices, directly reducing the value of LP positions.

2. **Broken allowlisted-user access:** If the admin allowlists individual user addresses but not the router, those users cannot use the router at all, breaking the core swap flow for the pool's intended participants.

Both outcomes represent broken core pool functionality with direct loss of LP principal above Sherlock thresholds.

---

### Likelihood Explanation

The bypass requires no special privilege. Any Ethereum address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The router is a public, factory-verified contract. The substitution is structural and deterministic — it fires on every router-mediated swap against any allowlisted pool. Likelihood is **High**.

---

### Recommendation

The extension must gate on the economically relevant actor — the human or contract that initiated the swap — not the immediate caller of `pool.swap`. Two complementary fixes:

**Option A — Pass the original initiator through the router.** Have `MetricOmmSimpleRouter` encode the real `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when present. This requires a convention between router and extension.

**Option B — Check `sender` against the allowlist and also accept the router as a transparent forwarder only when the router itself encodes the real initiator.** The extension reads the real initiator from `extensionData` when `sender == router`, falling back to `sender` for direct calls.

**Option C (simplest, most robust) — Require direct pool calls for allowlisted pools.** Document that `SwapAllowlistExtension` is incompatible with router-mediated flows and enforce this by reverting when `sender` is a known router address, forcing users to call `pool.swap` directly.

In all cases, add an integration test that exercises a router-mediated swap against an allowlisted pool with a non-allowlisted user and asserts `NotAllowedToSwap`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (or setAllowAllSwappers(pool, true) — either opens the router path).
  - Pool admin does NOT allowlist Alice (address(0xA11CE)).

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({
       pool: restrictedPool,
       zeroForOne: true,
       amountIn: X,
       ...
     });
  2. Router calls restrictedPool.swap(recipient, true, X, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Alice receives output tokens. LP balances decrease.

Expected: revert NotAllowedToSwap (Alice is not allowlisted).
Actual:   swap succeeds because the router is allowlisted, not Alice.
```

The `sender` value the extension receives is the router address: [7](#0-6) 

The allowlist lookup therefore resolves to `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][alice]`: [8](#0-7)

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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
