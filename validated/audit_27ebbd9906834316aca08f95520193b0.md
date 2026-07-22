### Title
`SwapAllowlistExtension` gates the router address instead of the actual user when swaps are routed through `MetricOmmSimpleRouter`, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the **router**, not the user. The extension therefore gates the router address rather than the actual swapper. If the pool admin allowlists the router (the only way to allow any router-mediated swap), every user — including non-allowlisted ones — can bypass the curated-pool restriction.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap` the hook is dispatched as:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument of the ABI-encoded `beforeSwap` call:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

**Step 2 — The extension checks that `sender` argument against the allowlist.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

`msg.sender` here is the pool (correct); `sender` is whoever called `pool.swap`.

**Step 3 — The router calls `pool.swap` without forwarding the original user.**

`exactInputSingle` stores the real user only in transient callback context (for payment), then calls the pool directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,   // ← recipient, not the user
        params.zeroForOne,
        ...
    );
``` [4](#0-3) 

The pool's `swap` receives `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same wrong-actor binding applies to `exactInput` and `exactOutput` multi-hop paths. [5](#0-4) 

---

### Impact Explanation

The pool admin faces an impossible choice:

| Router in allowlist? | Effect |
|---|---|
| **No** | Allowlisted users cannot use the router at all — core swap functionality broken for the supported periphery path |
| **Yes** | Every user, including non-allowlisted ones, can bypass the curated-pool restriction by routing through the router |

The second scenario is the fund-impacting one: a pool designed to restrict swaps to specific counterparties (e.g., KYC-gated, institutional-only) is fully open to any caller who routes through `MetricOmmSimpleRouter`. The allowlist extension provides zero protection on the router path.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported periphery for swaps. A pool admin who wants their allowlisted users to be able to use the router has no choice but to add the router to the allowlist, which immediately opens the pool to all users. The trigger is a normal, non-malicious admin action combined with any public user calling the router — both are routine operations.

---

### Recommendation

The extension must gate the **original user**, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trust assumption that the router is the only entry point, which is fragile.

2. **Add an explicit `originator` field to the swap interface**: The pool passes both `msg.sender` (the immediate caller) and an explicit originator address to the extension. The router sets originator = `msg.sender` before calling the pool. The extension gates on originator. This is the cleanest fix and mirrors how `DepositAllowlistExtension` correctly gates `owner` (the position owner) rather than `sender` (the immediate caller of `addLiquidity`). [6](#0-5) 

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; pool admin calls:
       extension.setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
       // router is NOT in the allowlist

2. alice calls router.exactInputSingle(pool, ...) → pool.swap(msg.sender=router) →
   extension checks allowedSwapper[pool][router] → false → revert NotAllowedToSwap
   ✗ alice cannot use the router despite being allowlisted

3. Pool admin, wanting alice to use the router, calls:
       extension.setAllowedToSwap(pool, router, true)

4. bob (not allowlisted) calls router.exactInputSingle(pool, ...) → pool.swap(msg.sender=router) →
   extension checks allowedSwapper[pool][router] → true → swap succeeds
   ✗ bob bypasses the allowlist entirely
```

The root cause is identical to the ZetaChain analog: the guard checks the **immediate caller** (`pool.swap`'s `msg.sender` = router) rather than the **economic actor** (the user who initiated the swap), so any intermediary contract that is itself allowlisted becomes a universal bypass vector.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
