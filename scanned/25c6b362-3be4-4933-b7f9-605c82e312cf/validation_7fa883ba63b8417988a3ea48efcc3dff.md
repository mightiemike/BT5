### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, letting any user bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument supplied by the pool. The pool always sets `sender = msg.sender`, which is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every non-allowlisted user can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — Extension checks `sender` (the router) against the allowlist.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the router) as the identity being gated: [2](#0-1) 

**Step 3 — Router calls the pool on behalf of the user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool therefore sees `msg.sender = router`, not the original user: [3](#0-2) 

**Step 4 — The allowlist is keyed by pool and swapper.**

The allowlist maps `pool → swapper → bool`. The swapper identity the extension actually checks is the router address, not the user: [4](#0-3) 

**The dilemma for the pool admin:**

| Admin choice | Effect |
|---|---|
| Allowlist the router | Every non-allowlisted user can bypass the gate by routing through the router |
| Do not allowlist the router | Allowlisted users cannot use the router at all; the supported periphery path is broken for them |

There is no configuration that simultaneously (a) allows allowlisted users to use the router and (b) blocks non-allowlisted users from doing the same.

---

### Impact Explanation

Any user can bypass a curated pool's swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). The pool admin's intent to restrict swaps to specific addresses (e.g., KYC'd counterparties, institutional traders) is completely defeated. The attacker can execute swaps on the curated pool, extracting value from LP positions that were priced assuming a restricted, trusted counterparty set. This is a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

The router is the primary user-facing swap interface documented and deployed alongside the pool. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is the natural, expected configuration. The bypass is therefore reachable on every production curated pool that supports router-mediated swaps, with no special preconditions beyond calling the public router.

---

### Recommendation

The extension must check the **original user**, not the intermediary. Two sound approaches:

1. **Pass the original user through the router.** Have the router forward the original `msg.sender` as an explicit `swapper` field inside `extensionData`. The extension decodes and checks that field instead of the `sender` argument.

2. **Check `sender` only when it is a direct pool caller; otherwise require the router to attest the real user.** Define a trusted-router registry in the extension; when `sender` is a known router, decode the real swapper from `extensionData`; otherwise check `sender` directly.

Either way, the invariant must hold: `allowedSwapper[pool][realUser]` is checked regardless of whether the user enters through the router or calls the pool directly.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension
  alice = allowlisted KYC user
  bob   = non-allowlisted attacker
  router = MetricOmmSimpleRouter (allowlisted so alice can use it)

Admin actions:
  extension.setAllowedToSwap(pool, alice,  true)   // alice is allowed
  extension.setAllowedToSwap(pool, router, true)   // router must be allowed for alice to use it

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob despite bob not being allowlisted

Result:
  bob swaps on a curated pool that was supposed to block him.
  The allowlist provides zero protection once the router is allowlisted.
``` [2](#0-1) [1](#0-0) [5](#0-4)

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
