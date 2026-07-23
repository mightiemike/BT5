### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user on the network. Conversely, a pool admin who does not allowlist the router locks out all allowlisted users from the supported periphery path.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool: [1](#0-0) 

The pool sets `sender = msg.sender` of its own `swap()` call: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. In every case the pool receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`.

---

### Impact Explanation

**Scenario A — Allowlist bypass (High):** A pool admin configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses and allowlists the router so that those addresses can use the standard periphery. Because the check resolves to `allowedSwapper[pool][router]`, every user on the network can call `router.exactInputSingle()` and pass the check. The allowlist is completely defeated; any unprivileged user can trade on a pool that was intended to be curated.

**Scenario B — Unusable swap flow (Medium):** A pool admin allowlists specific EOAs but does not allowlist the router. Those EOAs cannot use `MetricOmmSimpleRouter` at all — every router-mediated swap reverts with `NotAllowedToSwap` even though the real user is on the allowlist. The supported periphery path is broken for every allowlisted participant.

Both outcomes are contest-relevant: Scenario A is an admin-boundary break reachable by any unprivileged caller; Scenario B is broken core swap functionality for the intended users.

---

### Likelihood Explanation

The trigger is a standard, publicly documented periphery path (`MetricOmmSimpleRouter`). Any user who calls `exactInputSingle` or any other router entry point on a pool with `SwapAllowlistExtension` active reaches the vulnerable check. No special privilege, flash loan, or oracle manipulation is required. The likelihood is high whenever a pool is deployed with this extension and the router is the intended entry point.

---

### Recommendation

The extension must check the **economic actor**, not the immediate caller. Two complementary fixes:

1. **Pass the real user through the router.** The router already knows `msg.sender` (the real user). It should forward this identity in `extensionData` or via a dedicated field, and the extension should read it from there when the immediate `sender` is a known router.

2. **Check `sender` only when it is not a trusted router, otherwise check the user embedded in `extensionData`.** The pool admin configures a set of trusted routers; the extension falls back to the payload-embedded identity for those callers.

The simplest safe fix is to have the router pass the real user's address as the first word of `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a registered router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that router-mediated swaps are possible.
3. `attacker` (not on the allowlist) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `extension.beforeSwap(router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `attacker` successfully swaps on a pool that was supposed to be restricted to curated addresses.

Relevant check that resolves to the router address instead of the real user: [5](#0-4)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
