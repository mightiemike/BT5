### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` binds to `msg.sender` — the **direct caller of the pool**, not the end user. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract. If the pool admin allowlists the router address (the natural setup for enabling router-based swaps on a curated pool), the allowlist is completely bypassed: every user who calls the router can swap, regardless of whether they are individually allowlisted.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)
              msg.sender = router address
              → MetricOmmPool._beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        allowedSwapper[pool][router] → true → passes
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards it verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates the allowlist against that `sender`: [3](#0-2) 

When the router is the direct caller of `pool.swap`, `sender` = router address. The check `allowedSwapper[pool][router]` passes if the router has been allowlisted, regardless of who called the router.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

The router is a public, permissionless contract. Any EOA can call it.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the `MetricOmmSimpleRouter` address (to enable router-based swaps for their allowlisted users) inadvertently opens the pool to **all users**. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension will see `sender = router`, which is allowlisted, and permit the swap. The pool admin's curation policy — restricting swaps to specific counterparties — is completely nullified. This constitutes a **direct allowlist bypass on a curated pool**, allowing unauthorized users to trade against LP funds under terms the LPs did not consent to.

---

### Likelihood Explanation

The scenario is highly likely in practice:

1. A pool admin configures `SwapAllowlistExtension` to restrict swappers.
2. The admin also wants allowlisted users to be able to use the standard router (the primary UX path).
3. The admin allowlists the router address — a natural and expected configuration step.
4. From that point, any user can bypass the allowlist via the router.

The router is a deployed, public periphery contract. No special privileges or setup are required by the attacker beyond calling a standard public function.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economically relevant actor** — the end user — not the intermediary. Two complementary fixes:

1. **Pass the original caller through the router.** `MetricOmmSimpleRouter` should store `msg.sender` in transient storage at entry and expose it so the pool (or extension) can read the true originator. The pool would then pass this value as `sender` to extensions instead of its own `msg.sender`.

2. **Alternatively, gate on `recipient` or require the router to attest the real sender in `extensionData`.** Extensions could decode a signed or transient-storage-backed identity from `extensionData` rather than relying on the raw `sender` argument.

Until fixed, pool admins should **not** allowlist the router address on pools using `SwapAllowlistExtension`. Instead, allowlisted users must call `pool.swap` directly.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists the router so allowlisted users can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker: not individually allowlisted
address attacker = makeAddr("attacker");
token0.mint(attacker, 1_000e18);
token0.approve(address(router), type(uint256).max); // from attacker

vm.startPrank(attacker);
// Attacker routes through the router — sender seen by extension = router address
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: attacker,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds — allowlist bypassed
// allowedSwapper[pool][attacker] == false, but allowedSwapper[pool][router] == true
vm.stopPrank();
```

The swap succeeds because `SwapAllowlistExtension.beforeSwap` receives `sender = address(router)`, which is allowlisted, rather than `sender = attacker`, which is not. [5](#0-4) [6](#0-5) [7](#0-6)

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
