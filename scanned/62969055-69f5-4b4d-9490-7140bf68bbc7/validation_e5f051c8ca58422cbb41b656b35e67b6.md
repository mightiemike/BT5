### Title
SwapAllowlistExtension checks router address instead of actual user, enabling allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end-user. If the router is allowlisted — which is required for any router-mediated swap to succeed — every user, including non-allowlisted ones, can bypass the individual allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the caller of the extension), and `sender` is the value the pool received as `msg.sender` when its own `swap` function was called. [1](#0-0) 

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any of the `exact*` variants), the router itself calls `pool.swap(...)`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [4](#0-3) 

So `msg.sender` inside `MetricOmmPool.swap` is the **router address**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

For router-mediated swaps to work at all on an allowlisted pool, the pool admin **must** add the router to the allowlist. The moment the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of whether that caller is individually allowlisted or explicitly blocked.

The same structural problem applies to the multi-hop `exactInput` path, where intermediate hops also originate from the router:

```solidity
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY,
    i == 0 ? msg.sender : address(this), params.tokens[i]);
IMetricOmmPoolActions(pool).swap(...);
``` [5](#0-4) 

In every hop the pool sees `msg.sender = router`, so the extension always checks the router's allowlist entry.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market-makers) cannot simultaneously support `MetricOmmSimpleRouter` without opening the pool to every user of that router. Any address that is explicitly blocked or simply never added to the allowlist can trade freely by routing through the router. This breaks the core invariant of the extension — "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" — and constitutes a curation failure with direct trading-access consequences. [6](#0-5) 

---

### Likelihood Explanation

The bypass is reachable whenever a pool admin allowlists the router — a natural and expected operational step for any pool that intends to support the standard periphery UX. The pool admin has no way to allowlist the router for legitimate users while simultaneously blocking non-allowlisted users from using the same router. The trigger is therefore a routine admin action, not an exotic configuration. [7](#0-6) 

---

### Recommendation

The `SwapAllowlistExtension` must check the **ultimate user**, not the intermediary. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: Have `MetricOmmSimpleRouter` encode `msg.sender` into the `extensionData` it passes to the pool, and have `SwapAllowlistExtension.beforeSwap` decode and verify that address when `sender` is a known router.

2. **Dedicated sender-forwarding field**: Add an optional `originalSender` field to the extension interface or to a router-specific wrapper so the extension can always resolve the economic actor regardless of the call path.

Either approach must be authenticated (e.g., the extension only trusts the forwarded address when `sender` is a factory-registered router) to prevent a non-router caller from spoofing an allowlisted address through `extensionData`.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // KYC'd user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for router UX

Attack
──────
4. userB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      <allowlisted pool>,
           recipient: userB,
           ...
       })

5. Router calls pool.swap(userB, ...) → msg.sender in pool = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. Swap executes; userB receives output tokens.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

The root cause is at `SwapAllowlistExtension.sol` line 37: `allowedSwapper[msg.sender][sender]` where `sender` is the router, not the end-user. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-42)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
}
```

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-175)
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
