Audit Report

## Title
SwapAllowlistExtension checks router address instead of end user, allowing full allowlist bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to enable normal UX inadvertently grants every user on the network the ability to bypass the allowlist, completely breaking the access-control invariant.

## Finding Description
**Call chain:**

`MetricOmmPool.swap` passes `msg.sender` verbatim as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, so the pool sees `msg.sender = router`. The real end user is stored only in transient storage for callback settlement and is never forwarded to the extension: [4](#0-3) 

The same applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**The admin faces an inescapable dilemma:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps fail for **all** users, including allowlisted ones |
| Allowlist the router | **Every** user on the network can bypass the allowlist by calling the router |

There is no configuration that simultaneously allows allowlisted users to swap through the router and blocks non-allowlisted users. The wrong value is `allowedSwapper[pool][router]` being checked instead of `allowedSwapper[pool][realUser]`.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional traders, or protocol-controlled addresses). Once the pool admin allowlists the router to enable normal UX, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and the extension will see `sender = router` (allowlisted), granting the swap. The access-control invariant is fully broken: the allowlist no longer gates the economically relevant actor. This constitutes a broken core pool functionality (access-gating) and an admin-boundary break where an unprivileged path bypasses a configured restriction.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address — this is a natural, expected administrative action. The bypass requires no special privileges, no flash loans, and no malicious setup — only a call to a public router function. The condition is trivially reachable by any user.

## Recommendation
The extension must verify the **original end user**, not the intermediate router. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. `SwapAllowlistExtension` decodes and verifies it. The extension should also verify that `sender` (the direct pool caller) is a known, trusted router to prevent spoofing.

2. **Trusted-router registry**: The extension maintains a registry of trusted routers. When `sender` is a trusted router, the extension reads the real user from `extensionData`; otherwise it checks `sender` directly.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
3. Admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(...)
   → pool passes sender = router to beforeSwap
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap succeeds for Bob despite him not being allowlisted
```

Root cause: `SwapAllowlistExtension.beforeSwap` at line 37 checks `allowedSwapper[msg.sender][sender]` where `sender` is the router address, not the actual end user. [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
