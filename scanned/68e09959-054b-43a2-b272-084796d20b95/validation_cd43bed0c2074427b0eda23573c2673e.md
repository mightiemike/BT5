After reading the key extension files, pool swap flow, router, and liquidity adder, I have a clear picture of the hook plumbing. Let me trace the exact analog.

### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool level, so the extension checks the router's address — not the original user's address. A pool admin who allowlists the router (the only way to let allowlisted users trade via the router) simultaneously opens the pool to every user on the router, defeating the allowlist entirely.

### Finding Description

**Pool → Extension argument binding**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

**The allowlist check**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

**The router erases user identity**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — it does not forward the original `msg.sender` as a separate argument: [4](#0-3) 

At the pool level, `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**The forced admin dilemma**

For any allowlisted user to trade via the router, the admin must call `setAllowedToSwap(pool, router, true)`. Once that entry is set, every user — allowlisted or not — can call `router.exactInputSingle(pool, ...)` and pass the check, because the extension sees the router address and finds it allowlisted. There is no way to simultaneously (a) let allowlisted users use the router and (b) block non-allowlisted users from using the same router.

### Impact Explanation

Any non-allowlisted address can trade in a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist's purpose — restricting which counterparties interact with the pool — is completely nullified. Depending on the pool's pricing and fee configuration, unauthorized traders can extract LP value or disrupt the pool's intended market-making strategy. This is a direct loss of LP principal and a broken core pool functionality (access-controlled swap flow).

### Likelihood Explanation

Medium. The bypass requires the admin to allowlist the router, which is the natural production step when deploying a curated pool that is also expected to be accessible via the standard periphery. An admin who allowlists individual users and then adds the router to enable those users to trade via the UI inadvertently opens the pool to everyone. The admin has no way to detect this from the extension's interface alone.

### Recommendation

The router should forward the original caller's identity to the pool so the extension can gate on it. One approach: add an optional `originSender` field to the swap call or to `extensionData`, and have `SwapAllowlistExtension` read it when present. Alternatively, the extension should check both `sender` (direct caller) and a verified origin address extracted from `extensionData`, with the router signing or encoding the original `msg.sender` in a tamper-evident way. A simpler short-term fix is to document that allowlisting the router is equivalent to `allowAll`, and provide a separate per-user router wrapper that the pool can allowlist individually.

### Proof of Concept

```
Setup
─────
1. Pool deployed with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Admin calls setAllowedToSwap(pool, router, true)  // enable router for userA

Attack
──────
4. userB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: userB, ...})

5. Router executes:
       pool.swap(recipient=userB, ...)   // msg.sender = router

6. Pool calls:
       _beforeSwap(sender=router, ...)

7. Extension evaluates:
       allowedSwapper[pool][router] == true  →  check passes

8. Swap executes; userB receives output tokens from the allowlisted pool.
``` [3](#0-2) [4](#0-3) [1](#0-0)

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
