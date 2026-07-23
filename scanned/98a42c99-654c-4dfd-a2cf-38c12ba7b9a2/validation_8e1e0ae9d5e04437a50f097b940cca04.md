### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, allowing any user to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps by swapper identity. Its `beforeSwap` hook checks the `sender` argument forwarded by the pool, which is always `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps), every unprivileged user can bypass the per-user restriction by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, zeroForOne, ..., extensionData)   // msg.sender = router
              → ExtensionCalling._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to every before-swap hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called the pool — the router, not the end user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to inject the original `msg.sender` into the `sender` slot seen by extensions: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Consequence:** The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user — allowlisted or not — can swap through the router. Allowlist is nullified. |
| No | Allowlisted users cannot use the router at all. |

There is no configuration that enforces per-user restrictions for router-mediated swaps.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-only, institutional-only) and configures `SwapAllowlistExtension` to restrict swaps to approved addresses will naturally also allowlist the router so that approved users can use the standard periphery. Once the router is allowlisted, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will see `sender = router`, pass the check, and execute the swap — bypassing the intended per-user gate entirely. This is a direct admin-boundary break: an admin-configured guard is bypassed by an unprivileged path through a supported periphery contract, with fund-impacting consequences for LP positions in pools whose safety assumptions depend on restricting the counterparty set.

---

### Likelihood Explanation

Medium-High. Allowlisting the router is the expected operational step for any pool that wants to support the standard periphery. The pool admin has no documentation warning that doing so voids the per-user allowlist. The bypass requires only a standard router call — no special privileges, no flash loans, no multi-transaction setup.

---

### Recommendation

The extension must gate on the economically relevant actor, not the intermediary. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a signature or trusted-forwarder pattern so users cannot self-report a different address).
2. **Check `sender` and fall back to `extensionData`**: When `sender` is a known router, extract the real user from `extensionData`; otherwise check `sender` directly. This requires the router to always populate the field.

The simplest safe interim fix is to document that allowlisting the router is equivalent to `setAllowAllSwappers(true)` and remove the router from any curated pool's allowlist, requiring curated-pool users to call the pool directly.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // only Alice is approved
  pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for periphery support

Attack (Bob, not allowlisted):
  Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...)          // pool sees msg.sender = router
    → _beforeSwap(sender = router, ...)
    → SwapAllowlistExtension.beforeSwap(sender = router)
    → allowedSwapper[pool][router] == true → check passes
    → Bob's swap executes successfully

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
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
