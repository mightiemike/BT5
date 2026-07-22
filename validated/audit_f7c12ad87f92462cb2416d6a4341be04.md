### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool always passes `msg.sender` of its own `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual user. The extension therefore gates the router address, not the human trader. Any pool admin who allowlists the router to enable router-mediated swaps simultaneously opens the gate to every user on the internet, defeating the entire purpose of the allowlist.

---

### Finding Description

**Call chain:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  // checks router, not user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist: [2](#0-1) 

When the user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly with no mechanism to forward the original caller's address: [3](#0-2) 

So `sender` arriving at the extension is the router's address, not the user's address. The extension's allowlist lookup `allowedSwapper[pool][router]` is therefore checking the wrong actor.

**Two broken outcomes result from this mismatch:**

1. **Allowlist bypass (high impact):** If the pool admin allowlists the router address (the only way to let allowlisted users trade through the router), every address on the internet can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The curated pool becomes effectively open.

2. **Broken core functionality:** If the pool admin does *not* allowlist the router, every allowlisted user is silently blocked from using the router, even though they are individually permitted. The only path left is a direct `pool.swap()` call, which most users cannot make.

The same structural problem exists for `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`, all of which call `pool.swap` with `msg.sender = router`. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin intends to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers). The bypass allows any unpermissioned user to trade on the pool by routing through the public `MetricOmmSimpleRouter`. This is a direct loss of curation control and, depending on the pool's purpose, can result in:

- Unpermissioned users extracting value from the pool's liquidity at oracle-anchored prices.
- Regulatory or compliance failure for pools that require identity-gated access.
- LP principal at risk if the pool was designed to only accept trades from trusted counterparties.

This matches the **"allowlist bypass"** impact class in the contest scope: *"High direct loss or curation failure if disallowed users can still trade."*

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and deployed for the protocol. Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps (the normal user flow) will inevitably allowlist the router, triggering the bypass. The attacker needs no special privileges — only the ability to call a public function on a public contract. The trigger is a single `exactInputSingle` call.

---

### Recommendation

The pool must forward the original economic actor's address to extensions, not its own `msg.sender`. Two viable approaches:

1. **Pass original sender through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and checks it. This requires a convention between router and extension but avoids core changes.

2. **Add an explicit `originalSender` field to the swap call:** The pool's `swap` signature accepts an optional `originalSender` address (defaulting to `msg.sender` for direct calls). The pool passes this to extensions instead of `msg.sender`. The router populates it with `msg.sender`.

The `DepositAllowlistExtension` does not share this flaw because the pool's `addLiquidity` passes the `owner` parameter (set by the caller to the actual depositor) rather than `msg.sender` to the extension hook. [5](#0-4) 

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Admin allowlists the router so allowedUser can trade via router.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not allowlisted) calls the router directly.
// The extension sees sender = address(router), which IS allowlisted → passes.
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000,
        amountOutMinimum: 0,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Swap succeeds. Attacker bypassed the allowlist.
```

The root cause is that `allowedSwapper[pool][router]` is `true` (required for any router use), and the extension cannot distinguish which human called the router. [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
