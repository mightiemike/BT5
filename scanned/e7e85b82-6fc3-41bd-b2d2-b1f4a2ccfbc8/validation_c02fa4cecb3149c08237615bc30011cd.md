### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Per-User Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users swap through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks the router's allowlist status rather than the actual end-user's. If the pool admin allowlists the router (the natural step to enable router-based swaps for permitted users), every unpermitted user can bypass the per-user gate by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — Extension checks `sender` (the direct pool caller), not the originating user.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

**Step 3 — The router calls `pool.swap()` directly, making itself `msg.sender`.**

```solidity
// exactInputSingle — router is msg.sender of pool.swap()
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The identity mismatch:**

| Call path | `sender` seen by extension | Allowlist entry needed |
|---|---|---|
| User → `pool.swap()` directly | User address | `allowedSwapper[pool][user]` |
| User → `router.exactInputSingle()` → `pool.swap()` | Router address | `allowedSwapper[pool][router]` |

The pool admin intends to gate individual users. The extension is documented as "Gates `swap` by swapper address, per pool." [5](#0-4) 

To allow permitted users to use the router, the admin must add `allowedSwapper[pool][router] = true`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router — including users who were never individually permitted. The per-user allowlist is silently voided for the entire router-mediated path.

There is no mechanism in the router to forward the originating user's address to the extension in a trustworthy way; `extensionData` is user-controlled and cannot be relied upon for identity.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified traders, institutional counterparties, or whitelisted strategies) loses that restriction entirely for the router path. Any unpermitted user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool, bypassing the configured access control. Because the pool is oracle-anchored, the attacker trades at live oracle prices and can drain whichever side of the pool the oracle currently favors, or simply interact with a pool that was intended to be closed to them. The allowlist guard — the only mechanism protecting the pool's LP base from unauthorized swap flow — is rendered inoperative.

---

### Likelihood Explanation

- The router is the standard, publicly deployed periphery entry point; any user can call it.
- The pool admin must allowlist the router to let even permitted users trade through it, making the precondition a natural operational step rather than an edge case.
- No special privilege, flash loan, or multi-step setup is required; a single `exactInputSingle` call suffices.
- The bypass is silent: no event or revert signals that an unpermitted user succeeded.

---

### Recommendation

The extension must verify the originating user, not the intermediate caller. Two sound approaches:

1. **Pass the originating user through `extensionData` with router-level enforcement.** The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires the extension to trust only calls that arrive via the known router, which can be enforced by also checking `sender == router` and decoding the inner address.

2. **Check `sender` against the allowlist and require direct pool calls for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router; users must call the pool directly. This is operationally fragile but requires no code change to the extension.

The cleanest fix is option 1: the router should encode `msg.sender` into `extensionData` and the extension should decode and gate on that value, so the check is always against the true originating user regardless of intermediary.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is permitted
  admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack:
  bob (not in allowlist) calls:
    router.exactInputSingle({pool: pool, ...})
      → pool.swap(msg.sender = router, ...)
        → extension.beforeSwap(sender = router, ...)
          → allowedSwapper[pool][router] == true  → PASSES
  bob's swap executes successfully despite never being allowlisted.

Verification:
  bob calls pool.swap() directly:
    → extension.beforeSwap(sender = bob, ...)
      → allowedSwapper[pool][bob] == false → REVERTS NotAllowedToSwap
  The direct path correctly blocks bob; the router path does not.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
