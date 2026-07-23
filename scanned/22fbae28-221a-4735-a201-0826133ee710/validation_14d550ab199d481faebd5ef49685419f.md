### Title
`SwapAllowlistExtension` Checks Router Address Instead of Real User, Allowing Any Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and forwards that as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual user. Any pool admin who allowlists the router (the natural configuration for a permissioned pool that still wants to support the official periphery) inadvertently opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing the immediate caller as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so `sender` delivered to the extension is the router, not the originating user. The real user's address is only stored in the router's transient callback context (`_getPayer()`) and is never surfaced to the extension.

The `DepositAllowlistExtension` does not share this flaw — it correctly ignores `sender` and gates on `owner`, which is the economically relevant actor for deposits: [5](#0-4) 

---

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of addresses (KYC'd users, whitelisted market makers, etc.) while still supporting the official router will allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true` satisfies the check for **every** caller of the router, regardless of whether that caller is individually permitted. The allowlist is completely nullified for all router-mediated swaps. Users who are explicitly blocked can trade freely; the pool's curation policy is broken.

This is a direct loss-of-policy impact: the pool's access control is the mechanism protecting LP funds from unwanted counterparties. Bypassing it exposes LPs to trades they explicitly opted out of.

---

### Likelihood Explanation

The router is the primary user-facing entry point documented and deployed by the protocol. A pool admin who wants a permissioned pool but still wants users to access it through the standard UI/router will allowlist the router — this is the expected operational pattern. The bypass requires no special privileges, no malicious setup, and no non-standard tokens. Any unprivileged user can trigger it by calling `exactInputSingle` or `exactInput` on the router.

---

### Recommendation

The extension must gate on the **originating user**, not the intermediary. Two complementary fixes:

1. **Pass the real user through the pool.** Add an optional `originator` field to the swap call or extension context so the pool can forward the true initiator. The router would populate this with `msg.sender` before calling the pool.

2. **Check `recipient` or require direct pool calls for allowlisted pools.** As a short-term mitigation, document that pools using `SwapAllowlistExtension` must not allowlist the router; instead, each permitted user must call the pool directly. This is operationally limiting but closes the bypass immediately.

The `DepositAllowlistExtension` pattern (gating on `owner`, not `sender`) is the correct model: the economically relevant actor must be the one checked.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist Alice (address(0xA11CE))

Attack:
  1. Alice calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → msg.sender inside pool = router
  3. Pool calls _beforeSwap(router, ...)
  4. SwapAllowlistExtension.beforeSwap receives sender = router
  5. Check: allowedSwapper[pool][router] == true  → passes
  6. Swap executes. Alice, who is not allowlisted, has traded successfully.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
```

The `sender` the extension sees is the router address, not Alice's address, so the allowlist check is trivially satisfied for any user who routes through the official periphery.

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
