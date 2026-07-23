### Title
`SwapAllowlistExtension::beforeSwap` gates on the router address instead of the actual end-user, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is intended to restrict swaps to individually approved addresses per pool. However, the `beforeSwap` hook checks the `sender` argument — which is `msg.sender` of the `pool.swap()` call — not the originating end-user. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address. If the pool admin allowlists the router to enable router-mediated swaps, every user can bypass the individual allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong identity checked in the hook**

`SwapAllowlistExtension::beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap()` call.

**How the pool populates `sender`**

`MetricOmmPool::swap` passes `msg.sender` as the `sender` argument to the extension dispatcher:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

**How the router calls the pool**

`MetricOmmSimpleRouter::exactInputSingle` calls `pool.swap()` with the router as `msg.sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The identity mismatch**

| Swap path | `sender` seen by extension | Allowlist entry checked |
|---|---|---|
| User → `pool.swap()` directly | actual user address | `allowedSwapper[pool][user]` ✓ |
| User → Router → `pool.swap()` | **router address** | `allowedSwapper[pool][router]` ✗ |

If the pool admin allowlists the router (a natural action to support router-mediated swaps), the check becomes `allowedSwapper[pool][router] == true` for every user, regardless of whether that user is individually approved.

The `isAllowedToSwap` view function exposes the same flaw:

```solidity
function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
}
``` [5](#0-4) 

Calling `isAllowedToSwap(pool, user)` returns `false` for a non-allowlisted user, yet that user can still swap successfully through the router.

---

### Impact Explanation

The swap allowlist is a core access-control feature. Pools that deploy it expect only approved addresses to trade. Bypassing it via the router means:

- Any non-allowlisted address can execute swaps in a restricted pool by routing through `MetricOmmSimpleRouter`.
- The pool admin cannot simultaneously allow router-mediated swaps (by allowlisting the router) and restrict individual users — the two goals are mutually exclusive under the current design.
- Unauthorized traders introduce adverse selection against LPs in pools designed for curated counterparties, leading to direct LP principal loss.

This matches the allowed impact gate: **broken core pool functionality causing loss of funds** and **admin-boundary break where an unprivileged path bypasses a configured guard**.

---

### Likelihood Explanation

- The router is the primary user-facing entry point for swaps; pool admins who want their pools to be usable via the standard periphery will allowlist it.
- Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges.
- No on-chain signal distinguishes "router allowlisted to support router UX" from "router allowlisted to open swaps to everyone."

---

### Recommendation

The extension must gate on the **originating user**, not the intermediary. Two approaches:

1. **Pass the real caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check both `sender` and a caller field from `extensionData`**: The extension falls back to `sender` for direct swaps and reads the real caller from `extensionData` for router swaps.

3. **Document the invariant explicitly**: If the design intent is that the router is never allowlisted (users must swap directly), enforce this in the extension's `initialize` or admin setters by rejecting the router address.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router to support router-mediated swaps
vm.prank(poolAdmin);
ext.setAllowedToSwap(address(pool), address(router), true);

// Non-allowlisted user
address attacker = makeAddr("attacker");
assertFalse(ext.isAllowedToSwap(address(pool), attacker)); // attacker NOT allowlisted

// Attacker routes through the router — sender seen by extension = router (allowlisted)
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        recipient: attacker,
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds — allowlist bypassed
```

The `beforeSwap` hook receives `sender = address(router)`, finds it allowlisted, and

### Citations

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
