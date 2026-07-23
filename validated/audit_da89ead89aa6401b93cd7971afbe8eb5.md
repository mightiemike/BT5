Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the end user, enabling full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address rather than the end user's. A pool admin who allowlists the router to enable router-based trading for curated users inadvertently opens the gate to every unprivileged user.

## Finding Description
**Actor binding in the pool**

`MetricOmmPool.swap()` passes `msg.sender` as the first argument (`sender`) to `_beforeSwap`: [1](#0-0) 

**What the extension actually checks**

`SwapAllowlistExtension.beforeSwap` receives that `sender` value and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

**What the router passes as the caller**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — making the router itself `msg.sender` to the pool, and therefore `sender` inside the extension: [3](#0-2) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

**The structural trap**

Because the extension sees the router's address as `sender`, the pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot trade via the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the gate |

No existing guard in the extension or pool resolves this ambiguity. The `allowedSwapper` mapping is keyed by `(pool, sender)` with no mechanism to distinguish the economic actor behind the router call. [5](#0-4) 

## Impact Explanation
Any unprivileged user can trade on a curated pool intended to be restricted to a specific set of addresses. The allowlist — the sole access-control mechanism for swap gating — is rendered ineffective once the router is allowlisted. This constitutes a direct curation failure and can expose LP assets to trades from counterparties the pool admin explicitly excluded (e.g., sanctioned addresses, competitors, or users who have not completed KYC). This maps to broken core pool functionality causing potential loss of funds and an admin-boundary break where an unprivileged path bypasses the intended access control.

## Likelihood Explanation
The trigger is the pool admin allowlisting the router — a natural and expected operational step for any admin who wants their allowlisted users to use the standard periphery router. The bypass then requires no special privilege: any EOA calls `exactInputSingle` (or any other router entry point) through the router. The combination of a predictable admin action and a zero-skill exploit path makes this high likelihood once a curated pool is deployed with the router as a supported entry point.

## Recommendation
The extension must gate the **economic actor** — the end user — not the intermediary. Two complementary fixes:

1. **Pass the real user through the router**: The router should store `msg.sender` in transient storage (analogous to how it already stores the payer via `_setNextCallbackContext`) and include it in `extensionData`. The extension decodes and checks that address instead of `sender`.

2. **Expose a `swapOnBehalfOf` entry point**: The pool interface could expose a dedicated `swapOnBehalfOf(address user, ...)` entry point that the router calls, and the pool passes `user` as `sender` to extensions.

Option 1 is the cleanest fix given the existing transient-storage pattern already used in the router. [6](#0-5) 

## Proof of Concept
```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, alice, true).
   → alice is the only allowlisted user.
3. Pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true).
   → Admin allowlists the router so alice can trade via the standard periphery.

Attack
──────
4. bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool:      pool,
           recipient: bob,
           ...
       });

5. Router calls pool.swap(bob, ...) with msg.sender = router.

6. Pool calls _beforeSwap(sender=router, recipient=bob, ...).

7. SwapAllowlistExtension.beforeSwap checks:
       allowedSwapper[pool][router]  →  true   ✓ (admin allowlisted the router)

8. Swap executes. bob — who is not on the allowlist — successfully trades
   on the curated pool.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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
