### Title
SwapAllowlistExtension gates on the router's address instead of the actual end-user, making the guard permanently bypassable for all router-mediated swaps — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of `MetricOmmPool.swap`. When any user routes through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the actual trader. The allowlist therefore gates the wrong identity for every router-mediated swap, making the guard either permanently broken for allowlisted users (they cannot use the router) or trivially bypassable by any user (if the router is allowlisted).

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against its per-pool mapping:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is called, the router is the direct caller of `pool.swap`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

So `sender` arriving at the extension is always the router's address, never the end-user's address. For multi-hop `exactOutput`, the recursive callback path also calls subsequent pools from the router:

```solidity
// MetricOmmSimpleRouter.sol line 220-228
(int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
    .swap(
        msg.sender,   // ← still the router
        zeroForOne,
        ...
    );
```

This produces two mutually exclusive failure modes with no correct configuration in between:

**Mode A — allowlist individual users, not the router:**
Pool admin calls `setAllowedToSwap(pool, Alice, true)`. Alice calls `router.exactInputSingle`. The extension checks `allowedSwapper[pool][router]` → router is not allowlisted → reverts. Alice (a legitimately allowlisted user) cannot use the router at all.

**Mode B — allowlist the router to enable router-mediated swaps:**
Pool admin calls `setAllowedToSwap(pool, router, true)`. Now Bob (not allowlisted) calls `router.exactInputSingle`. The extension checks `allowedSwapper[pool][router]` → router is allowlisted → passes. Bob bypasses the allowlist entirely.

There is no configuration that simultaneously allows allowlisted users to swap through the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

- **Broken core swap flow**: Every allowlisted user is silently blocked from using `MetricOmmSimpleRouter` (the primary user-facing swap interface), making the router unusable for any pool that deploys `SwapAllowlistExtension` with per-user allowlisting.
- **Admin-boundary break**: If the pool admin allowlists the router to restore router access, the allowlist guard is completely neutralised — any unprivileged address can swap in a pool that was intended to be restricted. This is a direct admin-boundary bypass reachable by any public caller with no special privileges.

---

### Likelihood Explanation

- Every pool that deploys `SwapAllowlistExtension` with the intent to restrict swaps to specific users is affected.
- The bypass requires only a standard call to `MetricOmmSimpleRouter.exactInputSingle` — no special setup, no flash loans, no privileged access.
- The pool admin cannot detect or prevent the bypass without removing the router from the allowlist, which re-breaks legitimate router access for allowlisted users.

---

### Recommendation

The extension must check the actual end-user, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks that address. This requires a trusted router or a signed attestation.
2. **Check `recipient` instead of `sender` for swap allowlisting**: For single-hop swaps the recipient is often the real user; however this breaks for multi-hop paths where intermediate recipients are the router itself.
3. **Separate allowlist entries for direct vs. router-mediated swaps**: Document clearly that the allowlist only gates direct `pool.swap` callers and provide a companion extension that reads the real user from `extensionData` for router paths.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin: setAllowedToSwap(pool, Alice, true)
   — Alice is the only allowlisted swapper.

3. Alice calls router.exactInputSingle({pool: pool, tokenIn: T0, ...})
   → router calls pool.swap(recipient, ...)
   → pool calls _beforeSwap(router, ...)
   → extension checks allowedSwapper[pool][router] → false
   → reverts NotAllowedToSwap
   Alice (allowlisted) cannot use the router. ✗

4. Pool admin: setAllowedToSwap(pool, router, true)
   — Admin adds router to restore Alice's router access.

5. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...)
   → pool calls _beforeSwap(router, ...)
   → extension checks allowedSwapper[pool][router] → true
   → passes
   Bob (not allowlisted) swaps successfully. ✗
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
