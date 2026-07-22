### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the Actual Trader, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the **router address**, not the actual end user. If the router is allowlisted (or `allowAllSwappers` is set for the pool), every user — including those not individually allowlisted — can bypass the per-user swap restriction by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding `msg.sender` as the `sender` argument to every configured extension. [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and passes it verbatim to the extension's `beforeSwap` hook. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`. [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` = router address. The allowlist check becomes `allowedSwapper[pool][router]` — it never sees the actual end user's address.

This is structurally inconsistent with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks `owner` (the actual economic actor, explicitly passed by the caller) rather than `sender` (the payer/intermediary): [4](#0-3) 

The pool's `addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`: [5](#0-4) 

So the deposit allowlist correctly identifies the depositor regardless of who pays, while the swap allowlist cannot identify the actual trader when an intermediary router is involved.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd counterparties, institutional traders) is fully bypassed for any user who routes through `MetricOmmSimpleRouter` if the router address is allowlisted on that pool. Because the router is the canonical supported swap path, pool admins are likely to allowlist it. Once the router is allowlisted, the per-user restriction is entirely inoperative: every user who calls the router can trade on the restricted pool, regardless of their individual allowlist status. This constitutes a curation failure with direct fund-impacting consequences — unauthorized parties can drain pool liquidity at oracle-quoted prices.

Even without the router being explicitly allowlisted, the design is broken in the opposite direction: individually allowlisted users cannot swap through the router (their address is not the one checked), forcing them to call the pool directly — a path that may not be supported in production UIs.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint. Pool admins configuring a swap allowlist will naturally allowlist the router to enable normal trading flows, inadvertently opening the bypass to all router users. The trigger requires no special privileges — any user can call the router. The misconfiguration is a predictable consequence of the wrong-actor binding in the extension.

---

### Recommendation

Change `SwapAllowlistExtension.beforeSwap` to accept and check a caller-supplied "actual swapper" identity, analogous to how `DepositAllowlistExtension` checks `owner` rather than `sender`. One approach: check `recipient` (the address that receives output tokens) instead of `sender`, since `recipient` represents the actual economic beneficiary of the swap. Alternatively, require the router to pass the real user address through `extensionData` and have the extension decode and check it. The fix must ensure the checked identity cannot be spoofed by an unprivileged caller.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the `MetricOmmSimpleRouter` as a trusted intermediary.
3. Pool admin does **not** allowlist `attacker` (an address that should be blocked).
4. `attacker` calls `MetricOmmSimpleRouter.swap(...)` targeting the restricted pool.
5. The router calls `pool.swap(recipient=attacker, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
8. `attacker` successfully swaps on the restricted pool, bypassing the per-user allowlist entirely.

The check that should have blocked `attacker` — `allowedSwapper[pool][attacker]` — is never evaluated. [3](#0-2) [1](#0-0)

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
