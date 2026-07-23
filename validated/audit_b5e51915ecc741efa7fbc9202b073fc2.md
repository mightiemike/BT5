### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `sender`, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. If a pool admin allowlists the router address to enable router-mediated swaps for their permitted users, every unpermissioned user can bypass the allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

So `msg.sender` of `pool.swap()` = **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The same substitution occurs for `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) 

The `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the position owner explicitly supplied by the caller), which the `MetricOmmPoolLiquidityAdder` always sets to the actual depositor: [7](#0-6) [8](#0-7) 

The asymmetry is the root cause: the deposit guard checks the economically relevant identity (`owner`); the swap guard checks the transport identity (`sender` = direct caller of `pool.swap()`).

---

### Impact Explanation

A pool admin who deploys a permissioned pool (e.g., KYC-only, institutional-only) and wants allowlisted users to be able to use the public router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **every** user — including those not on the allowlist — can call `MetricOmmSimpleRouter.exactInputSingle()` and the extension will pass them through, because the check resolves to `allowedSwapper[pool][router] == true`. The allowlist is completely bypassed. Unauthorized users gain full access to restricted pool liquidity, violating the pool's access-control invariant and potentially exposing restricted LP capital to unintended counterparties.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a natural and expected configuration step: any admin who wants their KYC'd users to use the standard router will do exactly this, without realizing it simultaneously opens the gate to all users. The `MetricOmmSimpleRouter` is a public, permissionless contract. The configuration mistake is subtle and not documented as a hazard. Likelihood is **medium**.

---

### Recommendation

The `beforeSwap` hook should gate the economically relevant actor. Two options:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives output tokens and is set by the actual user even when routing through the router. However, this changes the semantics of the allowlist.

2. **Require the router to forward the original `msg.sender` in `extensionData`** and have the extension decode and verify it. This is the cleanest fix: the router encodes `msg.sender` into `extensionData`, and the extension verifies that encoded address against the allowlist.

3. **Mirror the deposit pattern**: introduce an explicit `swapper` parameter (analogous to `owner` in `addLiquidity`) that the router sets to `msg.sender` before calling `pool.swap()`, and have the pool forward it to the extension instead of its own `msg.sender`.

The minimal safe fix is option 2 or 3. Option 1 changes allowlist semantics and may not match admin intent.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is KYC'd)
  - allowedSwapper[pool][router] = true  (admin enables router for alice)

Attack:
  - bob (not KYC'd) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient=bob, ...)
  - pool.swap() calls _beforeSwap(msg.sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASS
  - Bob's swap executes on the restricted pool

Result:
  - Bob bypasses the KYC allowlist entirely
  - The allowlist guard is rendered ineffective for all router-mediated swaps
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-68)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
