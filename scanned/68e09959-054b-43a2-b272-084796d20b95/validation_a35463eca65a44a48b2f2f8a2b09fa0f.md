### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. The extension therefore checks whether the **router** is allowlisted, not the individual swapper. If the router is allowlisted (the natural configuration to enable periphery usage), every non-allowlisted user can bypass the curated-pool gate by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
              msg.sender = router
         → MetricOmmPool._beforeSwap(msg.sender=router, recipient, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks: allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`. The actual user's address is only stored in transient callback context for payment purposes and is never forwarded to the pool as the swap initiator: [4](#0-3) 

The same mismatch applies to `exactOutputSingle`, `exactInput`, and `exactOutput`.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router (to let approved users reach the pool via the standard periphery) inadvertently opens the gate to **every** user. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool; the extension sees `sender = router`, finds the router in the allowlist, and permits the swap. The individual allowlist is completely defeated. This constitutes a direct policy bypass on curated pools with fund-impacting consequences (non-KYC'd or otherwise restricted users can trade against LP capital that was intended to be gated).

---

### Likelihood Explanation

The likelihood is **high**. The router is the canonical periphery entry point documented and expected by users. A pool admin who wants to gate individual swappers but still support the standard router will naturally allowlist the router address, not realising that doing so grants universal access. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a call to the public router.

---

### Recommendation

The extension must check the **economically relevant actor** — the address that initiated the trade — not the intermediate contract that called the pool. Two complementary fixes:

1. **Pass the original initiator through the router.** `MetricOmmSimpleRouter` should forward `msg.sender` (the real user) as an explicit `initiator` field in `extensionData`, and `SwapAllowlistExtension` should decode and check that field when `sender` is a known router.

2. **Check `sender` only when it is not a trusted router, and check the decoded initiator otherwise.** Alternatively, redesign the hook signature so the pool always forwards the original EOA/contract that triggered the action, similar to how Uniswap v4 passes `msgSender` through the unlock path.

The simplest safe fix: `SwapAllowlistExtension.beforeSwap` should check `sender` against the allowlist **and** require that `sender` is not itself a factory-registered pool or known router — or the pool should be extended to carry the original initiator through the call stack.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (extension1) and beforeSwap order = 1.
  - Pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
    (to let approved users reach the pool via the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  attacker (not allowlisted) calls:
    router.exactInputSingle(ExactInputSingleParams{
        pool: curatedPool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    });

Trace:
  router → pool.swap(...)          // msg.sender = router
  pool   → _beforeSwap(sender=router, ...)
  ext    → allowedSwapper[pool][router] == true  → passes
  swap executes; attacker receives output tokens.

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
