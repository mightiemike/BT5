### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist on Curated Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router address**, not the original user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every unprivileged user can bypass the curated pool's swap allowlist by routing through the router.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards that value verbatim to every configured extension: [2](#0-1) 

**Step 2 — Router calls `pool.swap` directly, making itself `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` with no mechanism to forward the original caller: [3](#0-2) 

The same pattern holds for `exactInput` (multi-hop) and `exactOutput`: [4](#0-3) 

In every case, `msg.sender` to the pool is the **router contract**, so `sender` delivered to the extension is the router address.

**Step 3 — `SwapAllowlistExtension` checks `sender` (the router) against the allowlist.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [5](#0-4) 

`msg.sender` here is the pool (correct key for the mapping). `sender` is the router address. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The two failure modes:**

| Pool admin action | Result |
|---|---|
| Allowlists the router address | Every user bypasses the allowlist by routing through the router |
| Does NOT allowlist the router | No individually-allowlisted user can use the router at all |

Both outcomes break the curated-pool invariant. The first is the exploitable path.

### Impact Explanation
Any user can swap on a curated pool that was intended to be restricted to a specific set of addresses. The pool admin's allowlist is completely ineffective for router-mediated swaps. This is a direct policy bypass with fund-impacting consequences: disallowed counterparties can trade against the pool's liquidity, extracting value the pool admin intended to restrict.

### Likelihood Explanation
The router is the primary user-facing entry point documented and deployed by the protocol. Pool admins who configure a `SwapAllowlistExtension` will naturally allowlist the router to allow their approved users to trade. Once the router is allowlisted, the bypass is trivially reachable by any EOA with no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices.

### Recommendation
The `sender` argument passed to `beforeSwap` must represent the **economically responsible actor**, not the intermediary contract. Two complementary fixes:

1. **Router-side:** Have the router pass the original `msg.sender` as the `recipient`-equivalent "initiator" through `extensionData`, and update `SwapAllowlistExtension` to decode and check that value when present.
2. **Extension-side (short-term):** Document that `sender` is the direct caller of `pool.swap`, and require pool admins to allowlist the router only when combined with an off-chain or on-chain mechanism that restricts who may call the router for that pool. Alternatively, block the router from being allowlisted and require direct pool calls only.
3. **Long-term:** Introduce a first-class "initiator" field in the pool's swap call path so extensions always receive the original EOA regardless of routing depth.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist the router
  - Pool admin does NOT allowlist Alice (attacker)

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=Alice, ...)
     → msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes successfully for Alice despite her not being allowlisted

Result:
  Alice swaps on a curated pool she was never authorized to access.
  The pool admin's allowlist is completely bypassed.
``` [5](#0-4) [1](#0-0) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
