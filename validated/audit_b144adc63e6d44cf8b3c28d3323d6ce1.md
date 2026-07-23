### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's immediate `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router address (the natural configuration to enable official periphery usage), every user — including those the allowlist was designed to block — can bypass the per-user gate by routing through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`** [1](#0-0) 

The hook receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the caller of the extension), and `sender` is whatever the pool passed as the first argument to `beforeSwap`.

**How the pool populates `sender`** [2](#0-1) 

The pool calls `_beforeSwap(msg.sender, recipient, ...)`. `msg.sender` at that point is whoever called `pool.swap()`. [3](#0-2) 

`_beforeSwap` encodes that value as the `sender` argument forwarded to every extension.

**What the router sends** [4](#0-3) 

`exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The pool's `msg.sender` is therefore the **router contract address**, not the original EOA. The extension consequently checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**Contrast with the deposit allowlist (correctly implemented)** [5](#0-4) 

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and checks `owner` — the position owner explicitly passed by the pool. The `MetricOmmPoolLiquidityAdder` always forwards the real position owner, so the deposit gate is correctly bound to the economically relevant actor. The swap gate has no equivalent mechanism.

**The bypass**

A pool admin who wants to allow official router usage must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every swap that arrives through the router, regardless of who the original caller is. A user who is explicitly blocked (`allowedSwapper[pool][blockedUser] = false`) can trivially bypass the gate by calling `MetricOmmSimpleRouter.exactInputSingle` instead of `pool.swap` directly.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` because all of them call `pool.swap` with `msg.sender = router`. [6](#0-5) 

---

### Impact Explanation

A curated pool that relies on `SwapAllowlistExtension` to restrict trading to approved counterparties (e.g., KYC'd users, institutional partners, or whitelisted bots) loses that protection entirely once the router is allowlisted. Any user can drain LP-owned liquidity at oracle-derived prices, causing direct loss of LP principal. This matches the "allowlist bypass" and "wrong-actor binding" impact classes: unauthorized swappers reach the pool, and LP assets are at risk.

---

### Likelihood Explanation

Allowlisting the router is the expected operational step for any pool that wants to support the official periphery. A pool admin who sets `allowedSwapper[pool][router] = true` to enable normal user flows simultaneously opens the bypass for every non-allowlisted address. The trigger requires no special privilege beyond a normal `exactInputSingle` call through the public router.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **original initiating user**, not the immediate pool caller. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData` for each hop, and the extension decodes and checks that address. The pool admin must also configure the extension to trust the router as a forwarder.

2. **Dedicated router field**: Add an `originator` field to the `beforeSwap` interface so the pool can pass both the immediate caller and the original initiator, and the extension checks the originator.

Until fixed, pool admins should not allowlist the router address; instead, they must require all allowlisted users to call `pool.swap` directly.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension
// Admin allowlists the router (natural config for periphery support)
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// blockedUser is NOT individually allowlisted
// allowedSwapper[pool][blockedUser] == false

// blockedUser bypasses the allowlist via the router:
vm.prank(blockedUser);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: blockedUser,
        deadline: block.timestamp + 1,
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Swap succeeds: extension checked allowedSwapper[pool][router] == true
// blockedUser received output tokens despite being individually blocked
```

The pool's `_beforeSwap` receives `sender = address(router)`. The extension evaluates `allowedSwapper[pool][router] == true` and passes. The original `blockedUser` is never checked.

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
