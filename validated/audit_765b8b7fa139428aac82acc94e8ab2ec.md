All four cited files have been verified against the actual repository code. Every factual claim in the submission is confirmed:

- `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every extension [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` of the pool call [4](#0-3) 
- Same pattern in `exactInput`, `exactOutputSingle`, and `exactOutput` [5](#0-4) 

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end-user, enabling allowlist bypass or DoS — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to restrict which addresses may swap in a pool. Its `beforeSwap` hook checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, not the actual user. This creates two mutually exclusive failure modes: allowlisting the router bypasses per-user restrictions entirely, while allowlisting individual users prevents them from using the router at all.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` (L230–240). `ExtensionCalling._beforeSwap` forwards this value unchanged to `SwapAllowlistExtension.beforeSwap` (L149–177). The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the calling pool and `sender` is the router address (L37). `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly (L72–80), so the pool always sees `msg.sender = router`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Exploit flow (bypass path):**
1. Pool admin deploys pool with `SwapAllowlistExtension` in the `beforeSwap` slot.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Unprivileged attacker calls `router.exactInputSingle({pool: pool, recipient: attacker, ...})`.
4. Pool calls `extension.beforeSwap(router, attacker, ...)`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Swap executes; attacker receives tokens from a restricted pool.

**Exploit flow (DoS path):**
1. Admin calls `setAllowedToSwap(pool, alice, true)` (individual user allowlist).
2. Alice calls `router.exactInputSingle({pool: pool, ...})`.
3. Pool calls `extension.beforeSwap(router, alice, ...)`.
4. Extension checks `allowedSwapper[pool][router] == false` → reverts with `NotAllowedToSwap`.
5. Alice cannot use the router at all.

No existing guard distinguishes which end-user is behind the router call. The `recipient` field is not checked by the extension, and `extensionData` carries no caller identity by default.

## Impact Explanation
**Allowlist bypass (high-impact):** When the router is allowlisted to support the standard periphery flow, any unprivileged address can swap in a pool intended to be restricted to a curated set of counterparties. Restricted pools may hold concentrated liquidity at oracle-anchored prices for specific counterparties; opening them to arbitrary swappers exposes LP principal to uninvited adverse selection and drains owed LP assets. This is an admin-boundary break: an unprivileged path circumvents a pool role check.

**Core functionality break (DoS):** When individual users are allowlisted instead, those users cannot use `MetricOmmSimpleRouter` — the primary user-facing entry point — for any allowlisted pool, breaking the core swap flow.

## Likelihood Explanation
`SwapAllowlistExtension` is a production periphery extension. Any pool that deploys it and expects users to interact via `MetricOmmSimpleRouter` (the standard entry point) is immediately affected. No special attacker capability is required — a normal `exactInputSingle` call suffices. Both the extension and the router are the expected production setup, making this a realistic configuration.

## Recommendation
The extension must gate on the economically relevant actor, not the intermediate dispatcher. The cleanest fix is to have the router encode `msg.sender` into `extensionData` (e.g., `abi.encode(msg.sender)`) before calling the pool, and have the extension decode the true initiator from that field when `sender` is a known router address. Alternatively, document that pools using `SwapAllowlistExtension` must not allowlist the router and users must call the pool directly, but this breaks the standard UX and is not a code-level fix.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension in beforeSwap slot.
2. Admin: setAllowedToSwap(pool, router, true)
3. Attacker (not individually allowlisted) calls:
       router.exactInputSingle({
           pool: pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })
4. Pool calls extension.beforeSwap(router, attacker, ...)
   - msg.sender (pool) → allowAllSwappers[pool] == false
   - sender (router)  → allowedSwapper[pool][router] == true → PASSES
5. Swap executes. Attacker receives tokens from restricted pool.
   Per-user allowlist completely bypassed.
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
