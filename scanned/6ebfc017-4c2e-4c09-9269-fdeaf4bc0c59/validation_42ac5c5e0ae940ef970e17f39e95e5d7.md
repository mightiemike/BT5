### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any Caller to Bypass the Per-Pool Swap Allowlist via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the originating user. If the pool admin allowlists the router address (a natural configuration to enable router-mediated swaps for intended users), every unprivileged caller can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value the pool forwarded: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router is the direct caller of `pool.swap`: [4](#0-3) 

Therefore `sender` received by the extension equals the **router's address**, not the originating user. The allowlist lookup `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`.

A pool admin who wants their allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller who routes through it, regardless of whether that caller is on the intended allowlist.

The same substitution occurs in every multi-hop path: all intermediate and final hops are called by the router, so `sender` is always the router address across the entire path. [5](#0-4) 

---

### Impact Explanation

A pool restricted by `SwapAllowlistExtension` is typically deployed to limit trading to trusted counterparties (e.g., KYC'd market makers, whitelisted integrators) to prevent adverse selection against LPs. Once the router is allowlisted to serve those users, any unprivileged address can execute swaps on the restricted pool by calling through the router. This exposes LP capital to unrestricted adverse-selection flow, directly eroding LP principal — a medium-to-high direct loss of LP assets.

---

### Likelihood Explanation

The bypass requires the pool admin to have added the router to the allowlist. This is a natural and expected operational step: a pool admin who deploys a restricted pool and also wants their allowlisted users to access it via the standard router will allowlist the router address. The misconfiguration is not obvious because the admin believes they are enabling router access for their specific users, not for all users. The router is a public, permissionless contract, so any address can call it.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **original user**, not the intermediate caller. Two options:

1. **Pass the originating user through the router.** The router stores the real payer in transient storage (`_getPayer()`). The pool or extension could read this value. However, this requires a protocol-level convention.

2. **Check `recipient` or require the pool to pass the original `msg.sender` through a trusted forwarding mechanism.** The cleanest fix is to have the router pass the originating user as a verified field in `extensionData`, and have the extension decode and verify it (with the pool as the trusted source of `msg.sender`).

3. **Document that allowlisting the router is equivalent to `allowAllSwappers`.** If the design intent is that the router is always open, the allowlist should explicitly warn admins that adding the router address opens the pool to all users.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // intends to allow only userA
3. Pool admin calls setAllowedToSwap(pool, router, true)  // intends to let userA use the router
4. userB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: restrictedPool,
           recipient: userB,
           ...
       })
5. Router calls pool.swap(recipient=userB, ...) — msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension checks allowedSwapper[pool][router] → true
8. Swap executes successfully for userB despite not being on the allowlist.
```

The allowlist guard is fully bypassed. `userB` receives output tokens from a pool that was intended to be restricted to `userA` only. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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
```
