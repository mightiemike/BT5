### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps on an allowlisted pool), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

**Pool passes `msg.sender` as `sender` to extensions.**

In `MetricOmmPool.swap`, the `sender` forwarded to `_beforeSwap` is `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` verbatim into the extension call:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

**`SwapAllowlistExtension` checks `sender` against the per-pool allowlist.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`.

**The router calls `pool.swap()` directly, so `sender = router`.**

In `MetricOmmSimpleRouter.exactInputSingle`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The router stores the real user in transient storage for callback settlement, but the pool only sees `msg.sender = router`. The extension therefore receives `sender = router`, not the actual user.

The same applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`. [5](#0-4) 

**The admin faces an inescapable dilemma:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps fail for **all** users, including allowlisted ones |
| Allowlist the router | **Every** user on the network can bypass the allowlist by calling the router |

There is no configuration that simultaneously allows allowlisted users to swap through the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional traders, or protocol-controlled addresses). Once the pool admin allowlists the router to enable normal UX, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and the extension will see `sender = router` (allowlisted), granting the swap. The access-control invariant is fully broken: the allowlist no longer gates the economically relevant actor.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address. This is a natural, expected administrative action. The bypass requires no special privileges, no flash loans, and no malicious setup — only a call to a public router function.

---

### Recommendation

The extension must verify the **original end user**, not the intermediate router. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and verifies it. This requires the extension to trust the router's encoding, so the extension should also verify that `sender` (the direct pool caller) is a known, trusted router.

2. **Check `sender` only when it is not a trusted router**: The extension maintains a registry of trusted routers. When `sender` is a trusted router, the extension reads the real user from `extensionData`; otherwise it checks `sender` directly.

---

### Proof of Concept

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

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37: `allowedSwapper[msg.sender][sender]` where `sender` is the router, not the actual user. [6](#0-5)

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
