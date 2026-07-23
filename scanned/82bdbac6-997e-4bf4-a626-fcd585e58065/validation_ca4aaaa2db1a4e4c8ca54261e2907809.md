### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Making the Allowlist Guard Bypassable via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is `msg.sender` inside `MetricOmmPool.swap` — the **direct caller of the pool**, not the originating user. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to **all** users, completely defeating the allowlist guard.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct) and `sender` is the value passed by the pool from its own `msg.sender`. In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

At that point, `msg.sender` inside `MetricOmmPool.swap` is the **router address**, so `sender` forwarded to the extension is the router, not the originating user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for the pool admin:

| Pool admin action | Effect |
|---|---|
| Allowlist individual users only | Allowlisted users **cannot** use the router (router not in allowlist → revert) |
| Allowlist the router (to enable router access) | **All** users bypass the allowlist via the router |

The extension's admin setter is named `setAllowedToSwap(pool, swapper, allowed)` and the extension is documented as "Gates `swap` by swapper address, per pool" — the clear intent is per-user gating, not per-router gating. [4](#0-3) 

---

### Impact Explanation

If the pool admin allowlists the router address (a natural and expected operational step to allow their curated users to use the supported periphery), any unprivileged user can bypass the swap allowlist entirely by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The allowlist guard is configured and deployed but its protection is misapplied: the identity it checks is the router's, not the user's. Unauthorized users gain full swap access to a pool that was intended to be restricted, potentially draining LP value through adversarial trading on a pool whose liquidity was sized for a trusted counterparty set.

---

### Likelihood Explanation

Medium-High. Any pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router (to allow their own users to use the standard periphery) triggers the bypass. This is a natural, expected operational action — the router is the primary supported swap entrypoint. The bypass requires no special privileges from the attacker: any EOA can call `MetricOmmSimpleRouter` directly.

---

### Recommendation

The extension must check the **originating user**, not the direct pool caller. Two viable approaches:

1. **Pass the real initiator via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, also verifying that `msg.sender` (the pool's direct caller) is a trusted router registered with the factory.

2. **Check `sender` only when it is not a known router**: The factory maintains a registry of trusted routers; the extension falls back to checking the recipient or a decoded initiator when `sender` is a registered router.

Either approach must be paired with a factory-level registry so the extension cannot be spoofed by an attacker who deploys their own "router."

---

### Proof of Concept

```
Setup:
  pool = curated pool with SwapAllowlistExtension
  alice = allowlisted user
  bob   = non-allowlisted attacker

Pool admin actions:
  setAllowedToSwap(pool, alice,  true)   // allow alice
  setAllowedToSwap(pool, router, true)   // allow router so alice can use periphery

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool:        pool,
      recipient:   bob,
      ...
  })

  Router calls pool.swap(bob_recipient, ...) — msg.sender inside pool = router
  Pool calls _beforeSwap(sender=router, ...)
  Extension checks allowedSwapper[pool][router] → true  ✓
  Bob's swap executes despite not being allowlisted.
``` [5](#0-4) [6](#0-5) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-29)
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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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
