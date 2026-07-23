### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` of the direct `pool.swap()` caller. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to let allowlisted users use the standard periphery), any unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other `exact*` entry points) calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The result is a two-outcome trap for the pool admin:

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router at all; they must call the pool directly |
| **Does** allowlist the router | Every user — allowlisted or not — can bypass the gate by routing through the router |

There is no configuration that simultaneously lets allowlisted users use the router and blocks non-allowlisted users.

The `DepositAllowlistExtension` does **not** share this flaw: it gates by `owner` (the position owner passed as a parameter), which the liquidity adder preserves correctly regardless of who the payer is: [5](#0-4) 

---

### Impact Explanation

A pool admin deploys a curated pool (e.g., KYC-only, institutional-only) and configures `SwapAllowlistExtension` to restrict swaps to approved addresses. To let those approved users use the standard router interface, the admin allowlists the router contract. At that point, any unprivileged address can call `router.exactInputSingle` (or `exactInput` / `exactOutput`) and execute swaps on the restricted pool. The allowlist is completely neutralized. This is a direct admin-boundary break: an access-control invariant configured by the pool admin is bypassed by an unprivileged path through a supported periphery contract.

---

### Likelihood Explanation

The router is the canonical, documented swap entry point for end users. A pool admin who wants allowlisted users to have a normal UX will allowlist the router. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices. Any user who has approved the router for their tokens can execute it immediately.

---

### Recommendation

The extension must gate on the **original end-user identity**, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` for every hop, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router. This requires coordination between the router and the extension.

2. **Direct-call-only policy**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by having the extension revert when `sender` is not an EOA or is not itself allowlisted, forcing allowlisted users to call the pool directly.

The simplest safe fix matching the `DepositAllowlistExtension` pattern is approach (1): the router passes `abi.encode(msg.sender)` as the first word of `extensionData`, and the extension decodes it as the authoritative swapper identity.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension (extension1 = SwapAllowlistExtension,
   beforeSwapOrder = 1).
2. Admin calls swapExtension.setAllowedToSwap(pool, alice, true).
   Alice is the only allowlisted swapper.
3. Admin calls swapExtension.setAllowedToSwap(pool, router, true).
   (Required so Alice can use the router.)
4. Add liquidity so the pool has depth.

Attack
──────
5. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool:        pool,
           recipient:   bob,
           zeroForOne:  true,
           amountIn:    1000,
           ...
           extensionData: ""
       }));

6. Router calls pool.swap(bob, true, 1000, ...) — msg.sender = router.
7. Pool calls _beforeSwap(sender=router, ...).
8. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
9. Bob's swap executes on the allowlisted pool despite never being allowlisted.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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
