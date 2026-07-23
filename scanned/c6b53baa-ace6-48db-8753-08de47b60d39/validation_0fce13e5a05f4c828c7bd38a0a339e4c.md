### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the pool forwards the router's address as `sender` to the extension. The extension then checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. A pool admin who allowlists the router to enable standard periphery usage inadvertently opens the pool to every user, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension:

```solidity
// ExtensionCalling.sol line 163-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)   // sender == msg.sender of pool.swap()
)
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded address:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()`. When the user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The router never passes the original user's address into the pool call. The pool sees `msg.sender == router`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates two mutually exclusive failure modes:

1. **Broken functionality:** Pool admin allowlists specific users (not the router). Those users cannot swap through the router even though they are individually authorized. The standard periphery path is unusable for them.

2. **Allowlist bypass:** Pool admin allowlists the router address to enable periphery usage. Now every user — including those the allowlist was designed to exclude — can swap through the router without restriction.

The `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the position owner passed explicitly by the caller), not `sender`, so the liquidity adder path correctly gates on the economic beneficiary.

---

### Impact Explanation

**Impact: High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional traders) is fully bypassed the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's LP positions. LPs who deposited under the assumption of a restricted counterparty set suffer direct, unintended exposure. The allowlist provides zero protection on the router path.

---

### Likelihood Explanation

**Likelihood: Medium.** The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool operator who deploys `SwapAllowlistExtension` and also wants users to access the pool through the standard periphery will naturally allowlist the router, triggering the bypass. The operator has no on-chain signal that doing so opens the pool to all users.

---

### Recommendation

The extension must gate on the economically relevant actor, not the technical caller. Two approaches:

1. **Pass the original user through `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires a coordinated convention between router and extension.

2. **Check `sender` only when it is not a known router; otherwise decode the real user from `extensionData`:** The extension can maintain a registry of trusted routers and require those routers to attest the real user in `extensionData`.

3. **Mirror the deposit extension pattern:** Require callers to pass the real user address as a dedicated parameter (analogous to `owner` in `addLiquidity`) so the extension always sees the economic actor regardless of routing path.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to let allowlisted users reach the pool via the router.
3. Non-allowlisted attacker calls:
     MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(...) with msg.sender == router.
5. Pool calls _beforeSwap(msg.sender=router, ...).
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Attacker's swap executes on the curated pool with no allowlist enforcement.
```

The attacker address is never checked. The allowlist is completely bypassed for every user who routes through `MetricOmmSimpleRouter`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
