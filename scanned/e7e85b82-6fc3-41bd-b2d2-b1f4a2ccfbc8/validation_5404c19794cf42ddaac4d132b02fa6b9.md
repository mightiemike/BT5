### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the address the pool passes as the caller of `swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` received by the extension is the router address — not the actual end user. A pool admin cannot simultaneously allow allowlisted users to use the router and block non-allowlisted users: if the router is allowlisted, every user bypasses the guard; if it is not, no user can use the router at all.

### Finding Description

**Call path for a direct swap:**
```
user → pool.swap(...)
  pool: msg.sender = user
  _beforeSwap(msg.sender=user, ...)
  extension.beforeSwap(sender=user, ...)   ← correct actor checked
```

**Call path through the router:**
```
user → MetricOmmSimpleRouter.exactInputSingle(...)
  router → pool.swap(recipient, ...)
    pool: msg.sender = router
    _beforeSwap(msg.sender=router, ...)
    extension.beforeSwap(sender=router, ...) ← wrong actor checked
```

In `MetricOmmPool.swap`, the pool always passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called the pool — the router, not the user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender` into the pool's `sender` slot: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

### Impact Explanation

The pool admin faces an irresolvable dilemma:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert; allowlisted users cannot use the router |
| Router **allowlisted** | Every user — including those explicitly blocked — can bypass the allowlist by routing through the router |

In the second case, the `SwapAllowlistExtension` is completely defeated for the router path. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) targeting the curated pool and execute swaps that the pool admin intended to block. This is a direct loss of curation control and, depending on the pool's design (e.g., restricted counterparty pools, compliance-gated pools), can result in unauthorized fund flows and LP exposure to unintended counterparties.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint documented and supported by the protocol. Any user aware of the router can exploit this without any special privileges, tokens, or setup. The bypass requires only a single transaction.

### Recommendation

The extension must gate the **original user**, not the intermediary. Two approaches:

1. **Pass the original user through the router**: Have the router encode the original `msg.sender` in `extensionData` and have the `SwapAllowlistExtension` decode and check it. This requires a protocol-level convention for trusted routers.

2. **Check `sender` against a per-pool router registry and fall back to the original user**: The extension can recognize known router addresses and require the actual user identity to be supplied in `extensionData`, verified against a signature or trusted forwarder pattern.

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by reverting when `sender` is a known router, or by redesigning the extension to accept the real user identity from a trusted source in `extensionData`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only `alice`
  - Pool admin also allowlists the router address (so alice can use the router)

Attack:
  - `bob` (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: curated_pool,
          recipient: bob,
          ...
      })
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true (router was allowlisted)
  - Swap executes successfully for bob despite bob not being allowlisted

Result: bob trades on a pool that was supposed to be restricted to alice only.
``` [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
