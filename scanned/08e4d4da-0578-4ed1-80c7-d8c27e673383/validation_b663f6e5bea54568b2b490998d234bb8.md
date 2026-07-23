### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — which is `msg.sender` of the pool's `swap()` call — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (the only way to let users use the router at all), every unprivileged user can bypass the curated-pool allowlist by routing through it.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool passes `msg.sender` (its own caller) as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with itself as `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all.
- **Allowlist the router** → every user on the network can bypass the allowlist by routing through it.

The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd or whitelisted addresses loses that protection entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and trade against the pool's LP reserves. This constitutes direct unauthorized access to LP funds: the pool receives the wrong counterparty's tokens and pays out LP-owned tokens to an actor the allowlist was designed to exclude. The loss is bounded only by available liquidity and the attacker's capital.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who deploy a `SwapAllowlistExtension` pool and want their allowlisted users to use the standard router will inevitably allowlist the router address. The bypass requires no special privileges, no flash loans, and no multi-block setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must check the economic actor, not the immediate pool caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.

2. **Check `sender` only when `sender` is not a known router; otherwise check a user field from `extensionData`**: Fragile and requires router enumeration.

3. **Preferred — check `recipient` or require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` pools must not be used with the public router, or redesign the extension to accept a signed user identity in `extensionData` that the router forwards from `msg.sender`.

The cleanest fix is for the router to forward the original `msg.sender` as part of `extensionData` and for the extension to decode and verify it, so the checked identity is always the economic actor regardless of routing path.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension wired as beforeSwap hook.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     (necessary so that allowlisted users can use the router).
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. attacker calls MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  5. Router calls pool.swap(attacker, true, X, ...) with msg.sender = router.
  6. Pool calls _beforeSwap(router, attacker, ...).
  7. Extension checks allowedSwapper[pool][router] → true → no revert.
  8. Swap executes; attacker receives LP-owned token1 without being on the allowlist.

Result: allowlist guard silently fails open for every user who routes through
        MetricOmmSimpleRouter, regardless of whether they are individually allowlisted.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
