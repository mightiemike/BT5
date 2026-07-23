### Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router address (a natural step to let allowlisted users trade via the router), every unprivileged user can bypass the curated-pool gate by routing through the same router.

---

### Finding Description

**Actor binding in the pool**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**What the extension actually checks**

`SwapAllowlistExtension.beforeSwap` receives `sender` (first parameter) and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**What the router passes as the caller**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — making the router itself `msg.sender` to the pool, and therefore `sender` inside the extension: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**The structural trap**

Because the extension sees the router's address as `sender`, the pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot trade via the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the gate |

A pool admin who allowlists the router to enable router-based trading for their curated users inadvertently opens the gate to the entire public.

---

### Impact Explanation

Any unprivileged user can trade on a curated pool that is intended to be restricted to a specific set of addresses. The allowlist — the sole access-control mechanism for swap gating — is rendered ineffective. This constitutes a direct curation failure and, depending on pool design, can expose LP assets to trades from counterparties the pool admin explicitly excluded (e.g., sanctioned addresses, competitors, or users who have not completed KYC).

---

### Likelihood Explanation

The trigger is the pool admin allowlisting the router — a natural and expected operational step. Any admin who wants their allowlisted users to be able to use the standard periphery router will perform this step. The bypass then requires no special privilege: any EOA calls `exactInputSingle` through the router. The combination of a predictable admin action and a zero-skill exploit path makes this high likelihood once a curated pool is deployed with the router as a supported entry point.

---

### Recommendation

The extension must gate the **economic actor** — the end user — not the intermediary. Two complementary fixes:

1. **Pass the real user through the router**: The router should forward `msg.sender` (the end user) as an additional field in `extensionData` and the extension should decode and verify it. Alternatively, the pool interface could expose a dedicated `swapOnBehalfOf(address user, ...)` entry point that the router calls, and the pool passes `user` as `sender` to extensions.

2. **Validate inside the extension using `recipient`**: For the simpler case where the recipient equals the user, the extension could check `recipient` instead of `sender`. However, this is fragile because `recipient` can be set to any address.

The cleanest fix is option 1: the router stores `msg.sender` in transient storage (analogous to how it already stores the payer) and includes it in `extensionData`; the extension decodes and checks that address.

---

### Proof of Concept

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
