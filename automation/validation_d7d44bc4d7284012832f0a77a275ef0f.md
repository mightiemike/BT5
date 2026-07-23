### Title
Router-Mediated Swaps Corrupt Swapper Identity in `SwapAllowlistExtension.beforeSwap` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the end user. The hook therefore checks whether the **router** is allowlisted, not the actual trader. This makes the allowlist either unbypassable for legitimate router users, or trivially bypassable for any attacker once the router is allowlisted.

---

### Finding Description

**Call path — direct swap:**

```
user → pool.swap()
  msg.sender = user
  _beforeSwap(sender = user, ...)
  SwapAllowlistExtension.beforeSwap: allowedSwapper[pool][user]  ✅
```

**Call path — router swap:**

```
user → router.exactInputSingle()
  router → pool.swap()
    msg.sender = router
    _beforeSwap(sender = router, ...)
    SwapAllowlistExtension.beforeSwap: allowedSwapper[pool][router]  ❌
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim and forwards it to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router address when the call came through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

The same identity loss occurs in `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

Two concrete failure modes arise:

1. **Allowlist bypass (attacker path):** A pool admin allowlists the router address so that their allowlisted users can swap via the router. Any unprivileged attacker can then call `router.exactInputSingle` and pass the allowlist check, because the hook sees `sender = router` (allowlisted) regardless of who the actual caller is.

2. **Broken core functionality (legitimate user path):** A pool admin allowlists specific user addresses but not the router. Those users cannot swap through the router at all — the hook sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`. There is no configuration that simultaneously allows specific users via the router while blocking others.

There is no way to correctly configure `allowedSwapper` to distinguish between different end users routing through the same router contract.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production extension designed for pools that need access control. Any such pool whose admin allowlists the router (a natural step to support the official periphery) is immediately vulnerable to bypass by any address. The router is a public, permissionless contract.

---

### Recommendation

The extension must receive the original transaction initiator, not the immediate pool caller. Options:

- Pass `tx.origin` as an additional parameter in the hook data (with appropriate trust assumptions documented).
- Require callers to embed their identity in `extensionData` and have the extension verify it against a signed attestation or a trusted forwarder registry.
- Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level by rejecting pool configurations that combine this extension with a known router allowlist entry.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to allow router users.
3. Attacker (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Pool calls `_beforeSwap(msg.sender = router, ...)`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes in a pool they were never meant to access.

**Note on the "paused pool" framing in the question:** The `swap` function carries a `whenNotPaused` modifier that reverts before `_beforeSwap` is ever reached when `pauseLevel != 0`. [6](#0-5) [7](#0-6) 

A paused pool cannot reach the hook at all, so the "paused-flow regression" framing is not a separate exploit path. The real and standalone vulnerability is the swapper identity mismatch on the live (unpaused) router path described above.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
