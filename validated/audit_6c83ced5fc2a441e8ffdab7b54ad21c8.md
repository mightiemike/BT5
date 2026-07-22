### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Full Allowlist Bypass via Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router (the natural configuration to enable router-based swaps), every unprivileged user can bypass the allowlist entirely by routing through the router.

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to every extension.**

In `MetricOmmPool.swap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first positional argument to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks that forwarded `sender` against the allowlist.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

`msg.sender` here is the pool (correct). `sender` is whoever called `pool.swap()` — the router when the user goes through the router.

**Step 3 — The router calls `pool.swap` directly, making itself the `sender`.**

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The router stores the real end-user address only in transient storage for the payment callback (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), but never passes it to `pool.swap` as `sender`. [5](#0-4) 

**Result — two broken states, both reachable by any user:**

| Pool admin configuration | Observed behaviour |
|---|---|
| Router allowlisted (`setAllowedToSwap(pool, router, true)`) | `allowedSwapper[pool][router] == true` → every user bypasses the allowlist via the router |
| Router NOT allowlisted | `allowedSwapper[pool][router] == false` → individually-allowlisted users are blocked when they use the router |

The same wrong-actor binding affects `exactInput` (multi-hop) and `exactOutput` / `exactOutputSingle` paths in the router, all of which call `pool.swap` with `msg.sender == router`. [6](#0-5) 

### Impact Explanation

An unprivileged user who is not on the allowlist can call `router.exactInputSingle(...)` targeting a curated pool. Because the extension sees `sender == router` and the router is allowlisted (the only way to make the router usable at all), the guard passes and the swap executes. The pool admin's access-control policy — the sole purpose of deploying `SwapAllowlistExtension` — is completely defeated. This is a direct admin-boundary break: an unprivileged path (the public router) bypasses a configured guard on every curated pool that relies on this extension.

### Likelihood Explanation

Likelihood is high. The router is the canonical user-facing entry point documented and deployed alongside the pool. Any pool admin who wants users to be able to swap through the router must allowlist it, which simultaneously opens the pool to every user. The exploit requires no special privileges, no unusual token behaviour, and no multi-transaction setup — a single `exactInputSingle` call suffices.

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary router. Two viable approaches:

1. **Pass the real initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks it. This requires a coordinated change to the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to forward the user address explicitly**: Add a dedicated field to the swap parameters (or a standardised prefix in `extensionData`) that the pool passes through to extensions, allowing the extension to distinguish router-mediated calls from direct calls.

Until fixed, pool admins should be warned that deploying `SwapAllowlistExtension` on a pool whose router is allowlisted provides no access control.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as a before-swap hook.
2. Pool admin allowlists the router:
       extension.setAllowedToSwap(pool, address(router), true);
3. Non-allowlisted EOA (attacker) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool:          pool,
           recipient:     attacker,
           zeroForOne:    true,
           amountIn:      1e18,
           amountOutMinimum: 0,
           priceLimitX64: 0,
           deadline:      block.timestamp,
           extensionData: ""
       }));
4. Call stack:
       router.exactInputSingle()
         → pool.swap(msg.sender=router, ...)
           → _beforeSwap(sender=router, ...)
             → SwapAllowlistExtension.beforeSwap(sender=router)
               → allowedSwapper[pool][router] == true  ✓  (guard passes)
         → swap executes, attacker receives token1
5. Attacker — not on the allowlist — successfully swaps on the curated pool.
``` [7](#0-6) [1](#0-0) [8](#0-7)

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
