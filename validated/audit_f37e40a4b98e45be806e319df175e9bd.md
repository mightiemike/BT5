### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument — which is `msg.sender` of the pool's `swap()` call — against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. As a result, the allowlist gates the router address rather than the actual swapper. If the pool admin allowlists the router to enable periphery-mediated swaps, every user on-chain bypasses the allowlist entirely. If the router is not allowlisted, every individually-allowlisted user is silently blocked from using the standard periphery.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` argument against the allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

So `sender` arriving at the extension is the router address, not the originating user. The allowlist lookup `allowedSwapper[pool][router]` is evaluated, not `allowedSwapper[pool][user]`.

**Contrast with `DepositAllowlistExtension`**, which correctly gates the position `owner` (the second argument) rather than `sender` (the first argument):

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

When `MetricOmmPoolLiquidityAdder` calls `pool.addLiquidity(owner, ...)`, the `owner` field still carries the user's address, so the deposit allowlist correctly gates the economically relevant party regardless of who the payer/caller is. The swap allowlist has no equivalent "originating user" field to fall back on.

---

### Impact Explanation

**Scenario A — Router allowlisted (complete bypass):** A pool admin who wants to enable router-mediated swaps for their allowlisted users adds the router to `allowedSwapper[pool][router]`. Because the extension checks the router address, every user on-chain can now call `router.exactInputSingle()` and pass the allowlist check. The allowlist is completely nullified: unauthorized users can swap against restricted LP positions, draining pool funds in ways the pool admin explicitly intended to prevent.

**Scenario B — Router not allowlisted (broken core functionality):** A pool admin allowlists specific users directly. Those users call `router.exactInputSingle()` — the standard periphery — and receive `NotAllowedToSwap` even though they are individually permitted. The only workaround is to implement `IMetricOmmSwapCallback` and call `pool.swap()` directly, which is not the intended user flow and is not supported by any deployed periphery contract. The swap path through the standard router is permanently broken for all allowlisted users.

Both scenarios are reachable by any unprivileged user (Scenario A) or any allowlisted user (Scenario B) without any privileged action beyond the pool admin's normal configuration of the allowlist.

---

### Likelihood Explanation

Medium. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through `MetricOmmSimpleRouter` — the primary periphery — immediately hits one of the two failure modes. Scenario B (broken functionality) is triggered by the default configuration (router not allowlisted). Scenario A (bypass) is triggered the moment the admin tries to fix Scenario B by allowlisting the router. Both are reachable through normal, expected usage of the protocol.

---

### Recommendation

Gate the originating user rather than the direct pool caller. The swap interface does not carry a separate "originating user" field the way `addLiquidity` carries `owner`. Two clean fixes exist:

**Option 1 — Check `tx.origin` (acceptable for allowlist-only pools):** Replace `sender` with `tx.origin` in the allowlist lookup. This correctly identifies the EOA initiating the transaction regardless of router intermediaries. It is safe here because the allowlist is an explicit access-control gate, not a reentrancy guard.

**Option 2 — Decode originating user from `extensionData`:** Require the router to embed the originating user in `extensionData` and have the extension decode and verify it. This avoids `tx.origin` but requires router cooperation.

Additionally, align the swap allowlist with the deposit allowlist's design: the deposit extension correctly ignores `sender` and gates `owner`; the swap extension should gate the equivalent economically relevant identity.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, userA, true)
  pool admin does NOT allowlist the router

Attack path (Scenario B — broken functionality):
  1. userA calls router.exactInputSingle({pool, recipient: userA, ...})
  2. router calls pool.swap(userA, zeroForOne, amount, limit, "", extensionData)
     → msg.sender at pool = router
  3. pool calls extension.beforeSwap(router, userA, ...)
     → sender = router
  4. extension checks allowedSwapper[pool][router] → false
  5. revert NotAllowedToSwap()
  userA is blocked despite being individually allowlisted.

Attack path (Scenario A — bypass):
  1. pool admin calls setAllowedToSwap(pool, router, true)  ← trying to fix Scenario B
  2. attacker (not allowlisted) calls router.exactInputSingle({pool, ...})
  3. router calls pool.swap(...)  → msg.sender = router
  4. extension checks allowedSwapper[pool][router] → true
  5. swap succeeds for the attacker
  Allowlist completely bypassed; attacker swaps against restricted LP positions.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
