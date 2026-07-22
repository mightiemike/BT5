### Title
SwapAllowlistExtension Gates the Router's Identity Instead of the Original Swapper, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the allowlist to every user on the network, because any caller can route through the same router address.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`: [4](#0-3) 

So the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every caller, regardless of who the actual end user is. Any non-allowlisted attacker can call `exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension will pass.

The same structural problem applies to the multi-hop `exactInput` path, where intermediate hops use `address(this)` (the router itself) as the payer: [5](#0-4) 

The `DepositAllowlistExtension` does **not** share this flaw — it gates by `owner` (position owner), which is correctly preserved through the `MetricOmmPoolLiquidityAdder` path: [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC-approved counterparties, institutional participants, or any other curated set loses that restriction entirely the moment the router is allowlisted. Any unprivileged address can execute swaps against the pool's liquidity, draining LP assets at oracle-quoted prices without the pool admin's consent. Because the pool's spread and notional fees are collected on every swap, the LP positions are directly exposed to uninvited order flow, including adversarial flow that the allowlist was designed to exclude.

---

### Likelihood Explanation

The pool admin must allowlist the router for this to be exploitable. This is a natural and expected operational step: without it, every allowlisted user is forced to call `pool.swap()` directly and loses access to multi-hop routing, ETH wrapping, permit flows, and slippage protection that the router provides. The extension interface and its documentation contain no warning that allowlisting the router collapses the allowlist to "allow all." A pool admin following the obvious deployment path will trigger the vulnerability.

---

### Recommendation

The extension must gate on the original end-user identity, not the immediate `pool.swap()` caller. Two sound approaches:

1. **Trusted-forwarder pattern**: The router encodes the original `msg.sender` in `extensionData`; the extension verifies the router's signature or identity before trusting the forwarded address.
2. **Recipient-based gating**: For swap allowlists, gate on `recipient` rather than `sender`. The recipient is the address that receives output tokens and is set by the end user, not the router.

The simplest safe fix is to add a `trustedForwarder` mapping to the extension and, when `sender` is a trusted forwarder, decode and verify the real user from `extensionData`.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension attached to BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC-approved
3. Pool admin calls setAllowedToSwap(pool, router, true)  // enable router for alice

Attack
──────
4. Bob (not KYC-approved, not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      pool,
           recipient: bob,
           zeroForOne: true,
           amountIn:  X,
           ...
       })

5. Router calls pool.swap(bob, true, X, ...) → msg.sender = router
6. Pool calls _beforeSwap(router, bob, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true → passes
8. Swap executes; Bob receives output tokens from the restricted pool.

Result: Bob bypassed the allowlist with zero privileged access.
``` [3](#0-2) [7](#0-6) [8](#0-7)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-113)
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
