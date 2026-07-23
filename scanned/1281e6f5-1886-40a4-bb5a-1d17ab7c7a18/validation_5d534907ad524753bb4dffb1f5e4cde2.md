### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` mediates a swap, the pool receives `msg.sender = router` and forwards it as `sender` to the extension. The extension therefore checks `allowedSwapper[pool][router]` rather than the actual end-user. If the pool admin allowlists the router address — a natural configuration to enable router-mediated access for their curated users — every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checks router, not user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**The broken invariant:** The pool admin intends to gate the economic actor (the end-user). The extension gates the immediate caller of `pool.swap()`. These are the same address only for direct pool calls; they diverge for every router-mediated swap.

This forces the admin into an impossible choice:

| Admin configuration | Result |
|---|---|
| Allowlist the router | Every unprivileged user bypasses the allowlist via the router |
| Do not allowlist the router | Allowlisted users cannot use the router at all |

Neither option correctly enforces "only my allowlisted users may swap."

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly ignores `sender` (the payer/caller) and checks `owner` (the position owner), which is the economically relevant actor for liquidity operations: [6](#0-5) 

The swap extension has no equivalent separation — it checks `sender`, which is the router when the router is used.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional partners, or users entitled to subsidized pricing) loses that protection entirely once the pool admin allowlists the router. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and swap against the pool at the pool's configured rates. If the pool offers favorable pricing (lower fees, tighter spreads) reserved for its allowlisted counterparties, unauthorized swaps extract that value from LP funds. This is a direct loss of LP principal and a broken core pool invariant: "A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, production-supported swap entrypoint documented in the periphery. A pool admin who wants their allowlisted users to be able to use the standard router interface will naturally allowlist the router address, not realizing this opens the pool to all users. The misconfiguration is a single `setAllowedToSwap(pool, router, true)` call — a plausible operational mistake. No special privileges, flash loans, or multi-transaction sequences are required; any user can exploit it in a single `exactInputSingle` call.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the actual end-user rather than the immediate caller. Two approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is trust-dependent.

2. **Check `recipient` instead of `sender`:** For single-hop swaps the recipient is the user. This is imperfect for multi-hop paths where the recipient of intermediate hops is the router itself.

3. **Preferred — mirror the deposit extension pattern:** Introduce a separate `swapper` parameter (analogous to `owner` in liquidity calls) that the pool populates from a caller-supplied argument rather than from `msg.sender`, allowing the router to forward the true user identity. The pool's `swap` signature would need a `swapper` field distinct from the callback payer.

Until fixed, pool admins should be warned never to allowlist the router address and should allowlist only individual end-user addresses for direct pool calls.

---

### Proof of Concept

```solidity
// Setup: pool admin deploys a curated pool with SwapAllowlistExtension
// Admin allowlists the router so their users can use the standard interface
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not individually allowlisted) bypasses the gate:
router.exactInputSingle(ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         token0,
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp,
    extensionData:   ""
}));
// Succeeds: extension checks allowedSwapper[pool][router] == true
// Attacker swaps on the curated pool without being individually allowlisted
```

The extension's check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router] == true`, bypassing the per-user gate entirely.

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
