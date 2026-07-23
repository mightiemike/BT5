### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool level. The allowlist therefore checks the router's address, not the actual user's address. If the router must be allowlisted for router-mediated swaps to work at all, every user gains access regardless of the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` at the pool boundary: [4](#0-3) 

The router never forwards the original caller's address to the pool. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

This creates an inescapable dilemma for the pool admin:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Every allowlisted user is blocked from using the router — broken functionality |
| Router **allowlisted** | Every non-allowlisted user can bypass the guard by routing through the router |

---

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for restricting which addresses may trade on a pool. When the router is involved, the guard checks the wrong identity. A pool admin who allowlists only a set of institutional counterparties and also allowlists the router (required for those counterparties to use the standard periphery) simultaneously opens the pool to every public user. The admin-configured access boundary is completely bypassed by an unprivileged path — any user who calls `MetricOmmSimpleRouter` can trade on a pool that is supposed to be restricted.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard public entry point for swaps. Any user aware of the router address can exploit this without any special privilege, capital, or sequencing. Likelihood is high.

---

### Recommendation

The extension must check the economically relevant actor, not the immediate `msg.sender` of the pool. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated convention between router and extension.
2. **Check `sender` against a router registry and fall through to a user-level check**: The extension recognises known router addresses and requires the router to have embedded the real user in `extensionData`.

The simplest safe fix is option 1: the router always prepends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when `sender` is a known router.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required so userA can use the router
4. userB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender at pool = router
6. Pool calls _beforeSwap(sender=router, ...)
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
8. userB's swap succeeds despite never being allowlisted.
```

The guard is bypassed at step 7 because the router's address, not `userB`'s address, is the value checked against the allowlist. [5](#0-4) [6](#0-5)

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
