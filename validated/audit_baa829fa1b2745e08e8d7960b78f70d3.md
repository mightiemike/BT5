### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End-User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user enters through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual end-user. The allowlist therefore gates the router address, not the individual user. Any pool admin who adds the router to the allowlist (the natural step to let their allowlisted users trade via the router) simultaneously opens the pool to every user on the network.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the `sender` parameter of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router is the direct caller of `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol – exactInputSingle
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the end-user. The allowlist lookup becomes `allowedSwapper[pool][router]`. A pool admin who wants their allowlisted users to be able to trade via the router must add the router to the allowlist. Once the router is allowlisted, the check passes for every caller of the router — the actual end-user identity is never inspected.

The same structural issue applies to the multi-hop `exactInput` path, where intermediate hops use `address(this)` (the router itself) as the payer: [5](#0-4) 

---

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is designed to restrict trading to a known set of counterparties (e.g., to prevent toxic flow, enforce KYC, or limit LP exposure). Once the pool admin adds the router to the allowlist — the necessary step to let their approved users trade via the standard periphery — the allowlist is effectively nullified. Any unprivileged user can call `exactInputSingle` on the router and swap against the pool's LP positions. This exposes LP funds to the full universe of traders the allowlist was meant to exclude, constituting a direct loss of LP principal through toxic or adversarial flow.

---

### Likelihood Explanation

The likelihood is high. The router is the canonical, documented swap entrypoint for the protocol. A pool admin who deploys a curated pool and wants their allowlisted users to use the standard UI/router will naturally add the router to the allowlist. The misconfiguration is not obvious: the admin sees "router is allowed" and "user is allowed" as two separate entries, not realizing that allowlisting the router grants access to all router callers. No special attacker capability is required — any EOA can call `MetricOmmSimpleRouter.exactInputSingle`.

---

### Recommendation

The extension must check the **economic actor** — the address that initiated the trade and will bear its cost — not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the router.** The router already stores the real payer in transient storage (`_getPayer()`). The pool could expose a `swapWithSender(address trueSender, ...)` entry point, or the extension could accept an ABI-encoded true sender inside `extensionData` and verify it against a router-signed proof.

2. **Check `sender` only for direct pool calls; reject router-mediated calls on allowlisted pools.** The extension can detect router-mediated calls by comparing `sender` against a registry of known periphery contracts and reverting unless the actual user is separately verified.

The simplest safe fix is to have the router forward the real `msg.sender` as part of `extensionData` and have the extension decode and check that value, with the pool verifying the router's identity via `onlyPool` before trusting the forwarded address.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is the only intended swapper.
  3. Pool admin calls setAllowedToSwap(pool, router, true) — to let alice use the router.

Attack:
  4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(recipient, zeroForOne, ...).
  6. pool._beforeSwap(msg.sender=router, ...) → extension.beforeSwap(sender=router, ...).
  7. allowedSwapper[pool][router] == true → check passes.
  8. Bob's swap executes against LP positions; allowlist is bypassed.

Result: Bob, an unprivileged user, trades against a pool that was supposed to be restricted to alice only.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
