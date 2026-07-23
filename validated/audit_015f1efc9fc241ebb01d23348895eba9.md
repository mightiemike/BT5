### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the router is allowlisted (a natural operational choice so that allowlisted users can use multi-hop routing), any unprivileged user can bypass the curated-pool gate by routing through the public router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...) [msg.sender = router]
              → MetricOmmPool.swap() calls _beforeSwap(msg.sender=router, ...)
                   → ExtensionCalling._beforeSwap encodes sender=router
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                             → checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router, not the end user: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls the pool directly with no forwarding of the original `msg.sender`: [4](#0-3) 

The actual user's address is never surfaced to the extension.

---

### Impact Explanation

**Allowlist bypass (High):** A pool admin who allowlists the `MetricOmmSimpleRouter` so that approved users can perform multi-hop swaps inadvertently opens the gate to every user. Any non-allowlisted address can call `router.exactInputSingle()` targeting the curated pool; the extension sees `sender = router` (allowlisted), passes the check, and the unauthorized swap executes against LP funds.

**Allowlisted users blocked (Medium):** Conversely, if the router is not allowlisted, individually approved users cannot use the router at all on this pool, breaking the expected multi-hop and exact-output swap flows.

Both outcomes violate the core invariant that only approved addresses may trade on a curated pool.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint documented and deployed for the protocol. Pool admins who configure a `SwapAllowlistExtension` and also want to support router-based swaps for their approved users will naturally allowlist the router. This is a standard operational pattern, making the bypass reachable by any unprivileged user with no special setup.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original payer/initiator through the router.** The router already stores the original `msg.sender` in transient storage as the payer. Expose it as an additional field in the swap call or in `extensionData` so the extension can read it.

2. **Check `extensionData` for a signed or router-attested user identity** in `SwapAllowlistExtension.beforeSwap`, similar to how `MetricOmmPoolLiquidityAdder` separates `payer` from `owner` and the deposit extension correctly gates on `owner`.

The deposit allowlist already demonstrates the correct pattern — it checks `owner` (the economic beneficiary) rather than `sender` (the intermediary): [5](#0-4) 

`SwapAllowlistExtension` must apply the same principle.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, address(router), true)
    (to allow approved users to use multi-hop routing)
  - Pool admin does NOT allowlist attacker EOA

Attack:
  - attacker calls router.exactInputSingle({pool: curatedPool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes; attacker receives output tokens from LP funds

Result:
  - Non-allowlisted attacker successfully swaps on a curated pool
  - SwapAllowlistExtension invariant broken: unauthorized user traded
``` [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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
