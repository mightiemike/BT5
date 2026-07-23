Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the address that called `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates the router's allowlist entry rather than the actual user's. Any pool admin who allowlists the router to permit router-mediated swaps inadvertently grants every user — including those explicitly excluded — the ability to bypass the per-user gate.

## Finding Description

**Root cause — `sender` collapses to the router address:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Router call path — router is `msg.sender` to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly. The pool therefore sees `msg.sender = router`, not the end user: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**The dilemma:** A pool admin who wants to restrict swaps to a KYC'd set AND allow those users to use the public router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the extension's check `allowedSwapper[pool][router] → true` passes for every caller of the router, including addresses explicitly excluded from the allowlist. [6](#0-5) 

**No existing guard compensates:** The extension has no mechanism to recover the original `msg.sender` from the router. The `extensionData` field is user-supplied and unauthenticated, so it cannot be trusted for identity. The `recipient` field is also user-controlled and not equivalent to the swapper.

## Impact Explanation
Any unprivileged user can swap against a pool configured with `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`, as long as the router is allowlisted. Pools using the allowlist to restrict counterparties (permissioned RWA pools, KYC-gated venues, pools with specific LP agreements) have their access control silently nullified. Unauthorized swappers can execute trades against the pool's liquidity, causing direct loss of LP principal through adversarial or unfavorable swap execution. This satisfies **Broken core pool functionality causing loss of funds** and **Admin-boundary break: unprivileged path bypasses role checks**.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is the primary public entry point for swaps.
- Any pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to use the router must allowlist the router, directly triggering the bypass.
- No special privileges, flash loans, or exotic token behavior are required — a standard router call suffices.
- The bypass is reachable by any unprivileged user in a single transaction.

Likelihood: **Medium** (requires the common and expected admin action of allowlisting the router).

## Recommendation
The extension must gate on the economically relevant actor — the end user — not the intermediary. Two sound approaches:

1. **Transient-storage attribution (preferred):** The router writes the real `msg.sender` into a transient slot before calling the pool; the extension reads that slot. This is tamper-resistant and consistent with the protocol's existing use of EIP-1153 transient storage for reentrancy guards in `MetricOmmSwapRouterBase`.

2. **Authenticated `extensionData` forwarding:** The router encodes the original `msg.sender` into `extensionData` and the extension verifies the call originated from a trusted router before accepting that value. This requires a registry of trusted routers in the extension.

## Proof of Concept
```
Setup:
  1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
  2. Admin calls setAllowedToSwap(pool, router, true)
     — necessary so allowlisted users can use the router.
  3. Admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. Attacker (not on allowlist) calls MetricOmmSimpleRouter.exactInputSingle(...)
     targeting the pool.
  5. Router calls pool.swap(recipient=attacker, ...).
  6. Pool calls _beforeSwap(sender=router, ...).
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASS.
  8. Swap executes. Attacker receives pool output tokens.

Expected: revert with NotAllowedToSwap().
Actual:   swap succeeds — allowlist bypassed via router.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
