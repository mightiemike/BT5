### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper when `MetricOmmSimpleRouter` intermediates, allowing any user to bypass the per-pool swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract, not the end user. The original user identity is permanently lost at the pool boundary. A pool admin who allowlists the router (the natural step to let users trade through it) inadvertently opens the gate to every address on-chain.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `sender` (the router) keyed against the pool (`msg.sender` inside the extension = the pool): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — there is no mechanism to inject the original `msg.sender` into the pool's `sender` slot: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Structural parallel to M-03:** In M-03, `vaultOwners[vaultId]` was overwritten on a second `grab`, erasing the original owner so `buy` returned the vault to the wrong address. Here, the original user identity is erased at the pool call boundary — the router's address silently replaces the user's address before the allowlist check executes.

---

### Impact Explanation

Two fund-impacting scenarios arise from the same root cause:

**Scenario A — Allowlist fully bypassed (Critical path):**  
A pool admin allowlists the router so that their permitted users can trade through it (`allowedSwapper[pool][router] = true`). Because the extension sees only the router address, every address on-chain can now call `exactInputSingle` and pass the allowlist check. The pool's access control is completely nullified; any user can execute swaps and drain LP liquidity at oracle-quoted prices.

**Scenario B — Allowlisted users locked out of the router:**  
A pool admin allowlists individual EOAs but does not allowlist the router. Those EOAs cannot use `MetricOmmSimpleRouter` at all — every router-mediated swap reverts with `NotAllowedToSwap`. Users are forced to call `pool.swap()` directly, losing slippage protection, multi-hop routing, and deadline enforcement provided by the router.

Both scenarios represent broken core pool functionality. Scenario A is the direct fund-loss path: LP assets are exposed to unrestricted swaps at oracle prices.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface; pool admins are expected to support it.
- Allowlisting the router is the only way to let users trade through it on a restricted pool — a natural and expected admin action.
- No special privilege, flash loan, or unusual token behavior is required; any EOA calling `exactInputSingle` triggers the bypass.
- The `SwapAllowlistExtension` is a production periphery contract, not a mock. [6](#0-5) 

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`** — `recipient` is the address that receives output tokens and is set by the end user in `ExactInputSingleParams.recipient`. This is harder to spoof and represents the economic beneficiary.

2. **Decode the real swapper from `extensionData`** — The router already forwards `extensionData` unchanged. The extension can require callers to ABI-encode the real swapper address in `extensionData` and verify it against a signature or a separate allowlist entry. The router would need to inject `msg.sender` into `extensionData` before forwarding.

3. **Pool-level `sender` override** — Add an explicit `swapper` parameter to `pool.swap()` that the pool validates against `msg.sender` (e.g., must equal `msg.sender` or be pre-approved), then pass `swapper` to extensions instead of `msg.sender`.

Option 1 is the lowest-friction fix given the current interface.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin enables router for permitted users
  allowedSwapper[pool][alice]  = true   // alice is a permitted user
  allowedSwapper[pool][bob]    = false  // bob is NOT permitted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: bob,
      ...
  })

  pool.swap(bob, ...) is called with msg.sender = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
  → swap succeeds; bob receives output tokens

Result:
  bob, a non-allowlisted address, executes a swap on a restricted pool.
  LP assets are transferred to bob at oracle-quoted prices.
  The allowlist guard is completely bypassed.
``` [3](#0-2) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-29)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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
