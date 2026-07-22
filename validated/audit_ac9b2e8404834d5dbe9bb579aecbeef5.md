### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the **direct caller of `pool.swap()`**. When users route through `MetricOmmSimpleRouter`, the router contract is the direct caller, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router address (the natural configuration for router-enabled pools) inadvertently grants unrestricted swap access to every user, completely defeating the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates two mutually exclusive failure modes for any pool configured with `SwapAllowlistExtension`:

| Admin configuration | Effect |
|---|---|
| Router allowlisted (to enable router-based swaps) | **Every user** can swap through the router — allowlist is fully bypassed |
| Individual users allowlisted (not the router) | Allowlisted users **cannot** use the router at all — core swap flow broken |

Neither configuration achieves the intended goal of "only allowlisted users may swap, regardless of entry point."

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the actual position owner), not `sender`: [5](#0-4) 

The inconsistency between the two extensions confirms this is a design flaw, not an intentional trade-off.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC'd traders, institutional partners, or to prevent adverse selection) and then allowlists the router to support standard router-based swaps will inadvertently allow **any unprivileged user** to swap through the router. The allowlist guard is silently bypassed. LP funds in the pool are exposed to unrestricted swap flow from actors the admin explicitly intended to exclude, leading to potential LP losses from adverse selection or regulatory non-compliance.

---

### Likelihood Explanation

Allowlisting the router is the natural and expected configuration for any pool that wants to support the standard periphery swap path. A pool admin who reads `setAllowedToSwap(pool, router, true)` as "enable router-based swaps for allowlisted users" will unknowingly open the pool to all users. The misconfiguration requires no attacker privilege — any user with access to the router can exploit it.

---

### Recommendation

The extension should check the **actual end user**, not the intermediary router. Two viable approaches:

1. **Check `recipient` instead of `sender`**: The `recipient` argument is the address that receives output tokens and is set by the end user. For single-hop swaps this is the user's address. However, for multi-hop swaps the intermediate recipient may be the router itself.

2. **Pass the real user through `extensionData`**: The router can encode `msg.sender` (the end user) into `extensionData`, and the extension can decode and verify it. This requires a trusted router or a signed attestation.

3. **Align with `DepositAllowlistExtension`**: If the intent is to gate by position/trade owner, add a dedicated `swapper` parameter to the pool's swap call (analogous to `owner` in `addLiquidity`) and check that instead of `sender`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)` — Alice is not individually allowlisted.
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient=alice, ...)` — `msg.sender` at the pool is the router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Alice, a non-allowlisted user, successfully swaps in a pool that was supposed to restrict her access. [6](#0-5) [7](#0-6) [4](#0-3)

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
