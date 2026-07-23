### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Per-User Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool's `swap()` is the **router contract**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`, making the per-user allowlist meaningless for any swap that enters through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly, making the router the `msg.sender` of the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is a binding mismatch: the extension sees the router address as the swapper, not the end user. The pool admin has two losing options:

1. **Do not allowlist the router** → all allowlisted users are blocked from using the router entirely.
2. **Allowlist the router** → every user on the network can swap through the router, completely defeating the per-user restriction.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the LP position holder), not `sender`, so it does not share this flaw: [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers) provides **zero enforcement** for any swap routed through `MetricOmmSimpleRouter`. An unpermissioned user calls `exactInputSingle` or `exactInput` on the router; the pool's `beforeSwap` hook sees the router address; if the router is allowlisted (the only way to make the pool usable via the router), the swap executes regardless of the caller's identity. LP funds in a restricted pool are exposed to unrestricted trading, which is the exact economic harm the allowlist was deployed to prevent.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entrypoint documented and deployed for the protocol.
- Any user can call it permissionlessly.
- The bypass requires no special role, no flash loan, and no multi-step setup — a single `exactInputSingle` call suffices.
- Pool admins who deploy `SwapAllowlistExtension` expecting per-user enforcement will naturally allowlist the router to keep the pool usable, unknowingly opening the bypass.

---

### Recommendation

Pass the **original end-user address** as `sender` to the pool's `swap()` call, or have the extension resolve the true initiator from transient storage. The cleanest fix is to have `MetricOmmSimpleRouter` store the real `msg.sender` in transient storage (it already uses transient storage for callback context) and expose it so the pool can forward it as `sender` to extensions, or alternatively have the extension read it directly. A simpler short-term fix is to add a `realSender` field to `extensionData` that the router populates and the extension reads, though this is less trustworthy than a pool-level solution.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = 1.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // must allowlist router for usability
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  1. alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
     → msg.sender of pool.swap() == address(router)
  3. pool._beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router] == true  → passes
  5. Swap executes for alice despite alice not being on the allowlist.

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
