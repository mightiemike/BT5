### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, so the extension checks whether the **router** is allowlisted rather than whether the **actual user** is allowlisted. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards this value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first parameter — the pool's caller: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so `sender = router`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The router itself has no access control — any EOA can call it: [5](#0-4) 

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

If the pool admin allowlists the router address (the natural step to let allowlisted users trade through the router), the check `allowedSwapper[pool][router] == true` passes for **every caller** of the router, regardless of whether that caller is on the allowlist. The swap allowlist — the sole on-chain mechanism for restricting who may trade against the pool — is completely neutralised. Any unprivileged user can drain pool liquidity at oracle prices, bypassing the intended access control. This is a direct loss-of-access-control impact on a core pool function.

---

### Likelihood Explanation

The scenario is the natural operational configuration: a pool admin deploys a pool with `SwapAllowlistExtension`, allowlists a set of trusted counterparties, and also allowlists the router so those counterparties can use the standard periphery. The bypass requires no special privileges — any EOA can call the public router. The router is already deployed and widely usable, so the attack surface is always present once the router is allowlisted.

---

### Recommendation

Pass the **original user** through the call chain rather than the immediate pool caller. Two concrete options:

1. **Preferred — add a `payer`/`originator` field to the swap call**: The router stores the original `msg.sender` in transient storage (already done for the callback payer context). Extend the pool's `swap` signature or the extension data to carry the originator, and have `SwapAllowlistExtension` check that value.

2. **Simpler — check `recipient` instead of `sender` for router-mediated swaps**: This is weaker because `recipient` is also caller-supplied, but it at least forces the router to forward the user's address.

The cleanest fix mirrors the `DepositAllowlistExtension` pattern for deposits, which correctly checks `owner` (the economically relevant party) rather than `sender` (the immediate caller). The swap allowlist should check the economically relevant swapper identity, not the intermediary contract.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls SwapAllowlistExtension.setAllowedToSwap(pool, router, true)
    // admin intends to let allowlisted users use the router
  // attacker (address X) is NOT in allowedSwapper[pool]

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
    pool: pool,
    zeroForOne: true,
    amountIn: 1_000e18,
    ...
  })

  → router calls pool.swap(recipient, zeroForOne, amount, ...)
  → pool: msg.sender == router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
      checks allowedSwapper[pool][router] == true  ✓
  → swap executes; attacker receives output tokens

Result:
  Attacker (not allowlisted) successfully swaps against the pool.
  allowedSwapper[pool][attacker] was never set to true.
  The SwapAllowlistExtension guard is fully bypassed.
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
