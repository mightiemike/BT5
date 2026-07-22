### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. If the pool admin allowlists the router address (which is necessary for any allowlisted user to use the router), every unprivileged address can bypass the swap allowlist by routing through the router.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]
``` [1](#0-0) 

The extension receives `sender = router` and checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router address. [2](#0-1) 

The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`. [3](#0-2) 

`ExtensionCalling._beforeSwap` encodes `sender` (the router) and dispatches it to the extension.

**The structural asymmetry with `DepositAllowlistExtension`:**

The deposit allowlist correctly gates by `owner` — the economic beneficiary of the position: [4](#0-3) 

The swap allowlist gates by `sender` — the technical caller — which is the router when users route through it. There is no mechanism for the router to forward the original caller's identity to the extension.

**The bypass path:**

1. Pool admin deploys a `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Pool admin allowlists Alice (`allowedSwapper[pool][alice] = true`).
3. Alice wants to use the router, so the admin also allowlists the router (`allowedSwapper[pool][router] = true`).
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The extension sees `sender = router`, which is allowlisted → Bob's swap succeeds.

The router is a single shared public contract. Allowlisting it to serve any one legitimate user opens the gate for every user. [5](#0-4) 

---

### Impact Explanation

Any unprivileged address can bypass a pool's `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. The pool admin's access-control intent (restricting swaps to specific addresses) is silently voided the moment the router is allowlisted. This breaks the core pool functionality that the extension is designed to enforce and constitutes an admin-boundary break: an access-control mechanism set by the pool admin is bypassed by an unprivileged path.

---

### Likelihood Explanation

The scenario is reachable under normal operational conditions. Any pool that:
- Deploys a `SwapAllowlistExtension` to restrict swappers, **and**
- Needs at least one allowlisted user to use the router (a common UX requirement)

…must allowlist the router, which immediately opens the bypass to all users. The attacker needs no special privileges, no malicious setup, and no non-standard tokens.

---

### Recommendation

The `SwapAllowlistExtension` should gate the **economic actor**, not the technical caller. Two approaches:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives swap output and is the economic beneficiary. This mirrors how `DepositAllowlistExtension` gates by `owner`.

2. **Decode the actual user from `extensionData`** — require the router to forward the original `msg.sender` in `extensionData`, and have the extension verify and use that identity. This requires a coordinated change in the router's `extensionData` forwarding.

Option 1 is simpler and consistent with the deposit allowlist design. Option 2 is more flexible but requires router cooperation.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension
// Admin allowlists Alice and the router (so Alice can use the router)
ext.setAllowedToSwap(pool, alice, true);
ext.setAllowedToSwap(pool, address(router), true);

// Bob (not allowlisted) bypasses the allowlist via the router
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1_000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Bob's swap succeeds: extension saw sender=router (allowlisted), not bob (not allowlisted)
```

The extension receives `sender = address(router)`, which passes the `allowedSwapper[pool][router]` check, and Bob's swap executes on a pool he was never meant to access.

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
