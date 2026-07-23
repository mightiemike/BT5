### Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the real swapper, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its allowlist against the `sender` argument it receives, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract**, not the end user. If the pool admin allowlists the router (the only way to let legitimate users swap through it), every unprivileged user can bypass the allowlist by calling the router instead of the pool directly.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that exact `sender` value against the per-pool allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other `exact*` entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The router stores the real user only in its own transient callback context (`_setNextCallbackContext(..., msg.sender, ...)`); that value is never forwarded to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`.

This creates an inescapable dilemma for the pool admin:

| Admin action | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Do **not** allowlist router | ✗ blocked | ✗ blocked |
| **Allowlist router** | ✓ allowed | **✓ allowed (bypass)** |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

Any user who is not on the pool's swap allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`, provided the pool admin has allowlisted the router (which is the only way to let legitimate users use the router). The allowlist — the sole access-control boundary for swap-restricted pools — is completely neutralised for the router path. Unauthorized swaps drain pool liquidity at oracle-derived prices, bypassing the curation policy the pool admin intended to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical production swap interface. Any pool that uses `SwapAllowlistExtension` and wants its allowlisted users to be able to use the router must allowlist the router contract. Once that is done, the bypass is unconditionally available to every address. No special privileges, flash loans, or multi-transaction setup are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must verify the **end user**, not the intermediary. Two complementary approaches:

1. **Pass the real user through the pool.** Add an optional `originator` field to the swap call or extension data, have the router populate it with `msg.sender`, and have the extension verify it. This requires a coordinated change across pool, router, and extension interfaces.

2. **Check `sender` and fall back to an `extensionData`-encoded originator.** The extension can decode a user address from `extensionData` when `sender` is a known router, and gate on that decoded address. The router must be required to supply a signed or authenticated originator payload.

Either way, the extension must never treat an allowlisted intermediary as a blanket pass for all users behind it.

---

### Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension as the beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // only Alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so Alice can use it

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          <pool>,
           recipient:     bob,
           zeroForOne:    true,
           amountIn:      X,
           ...
       })

5. Router calls pool.swap(bob, true, X, ..., extensionData)
   → msg.sender inside pool = router
   → _beforeSwap(router, bob, ...)
   → SwapAllowlistExtension.beforeSwap(router, bob, ...)
   → allowedSwapper[pool][router] == true  ← passes
   → swap executes for Bob

Result: Bob swaps on a pool he was explicitly excluded from.
Direct pool call by Bob would revert (allowedSwapper[pool][bob] == false).
``` [5](#0-4) [6](#0-5) [1](#0-0) [7](#0-6)

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
