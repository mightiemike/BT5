### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` as seen by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. Any user can bypass a curated pool's swap allowlist by routing through the public router if the router address is allowlisted, or conversely, allowlisted users are silently blocked from using the router at all.

---

### Finding Description

**Call chain when a user swaps via the router:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
         checks: allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap()` directly with no mechanism to forward the original user's address as `sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

---

### Impact Explanation

**Scenario A — Allowlist bypass (High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a set of approved addresses. To allow those approved users to use the standard periphery, the admin calls `setAllowedToSwap(pool, router, true)`. Because the extension checks the router's address rather than the originating user, **any unprivileged user** can now call `MetricOmmSimpleRouter.exactInputSingle` and pass the allowlist check. The curated pool's access control is completely defeated. Depending on the pool's purpose (e.g., institutional-only liquidity, rate-limited access), this allows unauthorized parties to drain LP value or extract favorable oracle-anchored prices.

**Scenario B — Broken functionality for allowlisted users (Medium):** If the admin does not allowlist the router, every allowlisted user who attempts to swap via the router is silently rejected with `NotAllowedToSwap`, even though they are individually approved. The router is the documented standard periphery path; blocking it for allowlisted users breaks the core swap flow.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it.
- A pool admin who wants allowlisted users to use the router has no choice but to allowlist the router address, which opens the gate to all users. This is a natural and expected admin action.
- No privileged access, no special token behavior, and no off-chain oracle manipulation is required. The attacker only needs to call `exactInputSingle` on the router targeting the curated pool.

---

### Recommendation

**Short term:** In `SwapAllowlistExtension.beforeSwap`, gate on the `recipient` or require the pool to pass the original initiator through a separate mechanism. Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level by rejecting pools that configure both a swap allowlist and a router-accessible extension order.

**Long term:** Introduce an `originator` field in the swap hook arguments (analogous to EIP-7702 or Uniswap v4's `hookData` originator pattern) so extensions can gate on the true economic actor rather than the immediate `msg.sender` of the pool.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Admin allowlists the router so alice can use it:
extension.setAllowedToSwap(pool, address(router), true);

// Attacker (bob, not allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Pool calls _beforeSwap(sender=router, ...)
// Extension checks allowedSwapper[pool][router] == true → passes
// Bob's swap executes despite not being on the allowlist.
```

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
