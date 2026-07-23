### Title
`SwapAllowlistExtension` Bypassed via Router: Any User Can Swap in Restricted Pools When Router Is Allowlisted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is `msg.sender` of the `pool.swap()` call — the **router contract**, not the end user. When a pool admin allowlists the `MetricOmmSimpleRouter` address (the only way to permit router-mediated swaps), every unprivileged user can bypass the per-user restriction by routing through the router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the admin has allowlisted the router address, the check passes for **every caller** regardless of who the actual end user is.

The router itself never forwards the originating user's address to the pool: [4](#0-3) 

There is no mechanism in the router to inject `msg.sender` (the real user) as the `sender` seen by the extension.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., a private market-making pool, a KYC-gated pool, or a pool with a curated set of LPs) loses its access control entirely once the router is allowlisted. Any unprivileged address can execute swaps against the pool's liquidity, draining LP value at oracle-derived prices without the pool admin's consent. This constitutes broken core pool functionality and potential direct loss of LP assets.

---

### Likelihood Explanation

The admin **must** allowlist the router if they want their permitted users to swap through the standard periphery interface. There is no alternative path: direct `pool.swap()` calls require the caller to implement `metricOmmSwapCallback`, which is non-trivial for EOAs. The moment the router is allowlisted — a routine and expected configuration — the bypass is universally reachable by any address at zero cost.

---

### Recommendation

Pass the originating user's address through the router to the pool, and have the pool forward it to extensions as a distinct `originator` field. Alternatively, `SwapAllowlistExtension` should check `recipient` (the economic beneficiary) rather than `sender` (the intermediary), or the extension interface should carry a separate `payer`/`originator` field that the router populates from `msg.sender` before calling the pool.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension

// Admin allowlists the router so "permitted" users can swap
ext.setAllowedToSwap(pool, address(router), true);

// Attacker — never individually allowlisted
address attacker = makeAddr("attacker");
token0.mint(attacker, 1e18);
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);

// Router call: pool.swap() sees msg.sender = router
// Extension checks allowedSwapper[pool][router] == true → passes
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp + 1,
    extensionData:   ""
}));
// Attacker successfully swaps in a pool they were never allowlisted for
vm.stopPrank();
```

The `NotAllowedToSwap` revert that `SwapAllowlistExtension` is supposed to enforce never fires because the checked identity is the router, not the attacker. [5](#0-4) [6](#0-5)

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
