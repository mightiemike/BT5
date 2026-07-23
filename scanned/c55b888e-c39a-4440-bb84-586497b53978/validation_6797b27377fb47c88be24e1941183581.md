### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, the pool receives the **router** as `msg.sender`, so `sender` passed to the extension is the router address, not the actual end user. If the router is allowlisted on the pool, every user of the public router bypasses the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that argument (`sender`) against the per-pool allowlist, using `msg.sender` (the pool) as the mapping key: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [4](#0-3) 

So the effective check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants allowlisted users to be able to trade through the supported periphery router must allowlist the router address. Once the router is allowlisted, **every caller of the public router** passes the check — the allowlist is completely bypassed. Conversely, if the router is not allowlisted, even individually allowlisted users cannot trade through the router, breaking the intended user flow.

The analog to the external report is exact: the router plays the role of the DAO proxy — a shared intermediary that holds a granted permission (allowlist entry) — and any unprivileged user can leverage it to perform an action the guard was supposed to block.

---

### Impact Explanation

**High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that protection entirely once the router is allowlisted. Any user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and trade against the pool's LP reserves without restriction. LP providers who deposited under the assumption that only vetted counterparties could trade against them are exposed to unrestricted adversarial flow, including sandwich attacks and directional drain, which constitutes direct loss of LP principal above contest thresholds.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is a public, permissionless contract. The bypass requires only that the pool admin allowlists the router — a natural and expected action for any pool that intends to support router-mediated swaps for its allowlisted users. The admin has no on-chain signal that allowlisting the router opens the pool to all users; the `isAllowedToSwap` view function returns `true` for `(pool, router)` and gives no indication of the broader consequence. [5](#0-4) 

---

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediary. Two sound approaches:

1. **Pass the original caller through `extensionData`**: the router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: for single-hop swaps the recipient is often the user, though this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Require direct pool interaction for allowlisted pools**: document that pools using `SwapAllowlistExtension` must not allowlist the router, and add a revert in the extension if `sender` is a known factory-registered router.

The cleanest fix is approach 1, making the allowlist check independent of the call stack depth.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. Attacker (non-allowlisted EOA) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })
  2. Router calls pool.swap(recipient, zeroForOne, amount, ...).
     msg.sender to pool = router.
  3. Pool calls _beforeSwap(router, recipient, ...).
  4. Extension checks allowedSwapper[pool][router] → true → passes.
  5. Swap executes. Attacker receives output tokens.

Result:
  Non-allowlisted attacker successfully swaps on a curated pool.
  allowedSwapper[pool][attacker] was never set to true.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L27-29)
```text
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
