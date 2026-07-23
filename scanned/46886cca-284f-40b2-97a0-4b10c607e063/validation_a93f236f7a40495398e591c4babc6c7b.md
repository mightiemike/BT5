Looking at the extension framework, I need to trace how `sender` flows from the router through the pool into the `SwapAllowlistExtension`, and compare it against how `owner` flows into `DepositAllowlistExtension`.

### Title
`SwapAllowlistExtension` checks the router's address as the swapper, allowing any user to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` inside `MetricOmmPool.swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router (the expected production setup so that users can swap through the router), every user — including those not individually allowlisted — can bypass the per-user gate by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` argument forwarded to every configured extension: [2](#0-1) 

**Step 2 — The router is `msg.sender` inside the pool.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. There is no mechanism to forward the original `msg.sender` (the end user) to the pool: [3](#0-2) 

So when the pool executes, `msg.sender == router`, and `sender` delivered to the extension is the router address.

**Step 3 — `SwapAllowlistExtension` checks `sender`, not the end user.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [4](#0-3) 

`msg.sender` here is the pool (the pool calls the extension), and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the router is in the allowlist, the gate passes for **every** end user who calls through the router.

**Step 4 — Contrast with `DepositAllowlistExtension`, which correctly checks the actual owner.**

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` (the direct caller) and checks `owner` — the address that will actually own the position: [5](#0-4) 

This is robust against intermediary callers. The swap extension has no equivalent "actual user" field to check.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional traders, or whitelisted market makers). To allow those users to interact via the standard router, the admin must add the router to the allowlist. Once the router is allowlisted, the per-user gate is completely neutralised: any address can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput` and the extension will approve the swap because it sees `sender == router`. The allowlist provides no per-user protection at all when the router is in use.

This is an admin-boundary break: an unprivileged path (the router) bypasses an access-control invariant that the pool admin explicitly configured.

---

### Likelihood Explanation

The router is the primary user-facing interface for the protocol. Any pool that (a) deploys `SwapAllowlistExtension` and (b) wants its allowlisted users to be able to use the router must add the router to the allowlist. This is the expected production configuration, making the bypass reachable in every realistic deployment of this extension.

---

### Recommendation

Replace the `sender` check with a check on `recipient` (the address receiving output tokens), or — better — add an explicit `swapper` field to the `beforeSwap` hook signature that the pool populates with the original `msg.sender` before any router indirection. Alternatively, mirror the `DepositAllowlistExtension` pattern: require callers to pass the actual user address as a named parameter (analogous to `owner`) so the extension can gate on the real actor rather than the intermediary.

As a short-term mitigation, document clearly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`, so pool admins are not misled into believing per-user restrictions remain effective.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured in `beforeSwapOrder`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice should be able to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)`. Inside the pool, `msg.sender == router`.
6. `_beforeSwap(router, recipient, ...)` is called; the extension checks `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes successfully, bypassing the per-user allowlist entirely.

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
