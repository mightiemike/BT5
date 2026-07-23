### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any unprivileged caller to bypass a curated pool's swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the allowlist to every user on the network.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`sender` is populated by `MetricOmmPool.swap` as `msg.sender` of the `swap()` call: [2](#0-1) 

which is forwarded verbatim through `ExtensionCalling._beforeSwap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension sees `sender = address(router)`, not the originating user. The allowlist check becomes `allowedSwapper[pool][router]`.

**The trap:** A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper`. The moment they do, `allowedSwapper[pool][router] == true` passes the check for **every** user who routes through the router — the allowlist is completely neutralised for that entry path.

The `DepositAllowlistExtension` does not share this flaw because it gates `owner` (the position beneficiary), which is passed explicitly and is not overwritten by the intermediary: [5](#0-4) 

---

### Impact Explanation

A curated pool that uses `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise vetted counterparties is fully bypassed. Any unprivileged user can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`, receiving output tokens at oracle-anchored prices. LP funds are consumed by unauthorized traders, and the pool's curation invariant — the core reason the admin deployed the allowlist — is broken. This constitutes a direct loss of LP assets and broken core pool functionality.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical, documented periphery swap path; users are expected to use it.
- A pool admin who deploys `SwapAllowlistExtension` and wants their allowlisted users to access the router **must** add the router to the allowlist — there is no other mechanism.
- Once the router is added, the bypass is unconditional and requires no special privileges, timing, or state manipulation: any user calls `exactInputSingle` on the allowlisted pool.
- The admin has no way to simultaneously allow router usage and enforce per-user identity checks with the current extension design.

---

### Recommendation

Pass the **originating user** through the swap path rather than the immediate `msg.sender`. Two concrete options:

1. **Extend the extension interface** to carry an `originator` field (set by the pool to `tx.origin` or supplied by the router as part of `extensionData`) so the allowlist can gate the economic actor rather than the intermediary.
2. **Check `extensionData` in the router** — have the router ABI-encode the real user address into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it, combined with a `msg.sender`-is-trusted-router guard to prevent spoofing.

The simplest safe fix consistent with the existing architecture is option 2: the router encodes `msg.sender` (the user) into `extensionData`; the extension decodes it and checks `allowedSwapper[pool][decodedUser]` only when `sender` (the pool-visible caller) is a known trusted router.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice] = true   // alice is the only intended swapper
  allowedSwapper[P][router] = true  // admin adds router so alice can use it

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: P,
      zeroForOne: true,
      amountIn: X,
      recipient: bob,
      ...
  })

  Router calls P.swap(bob, true, X, ..., "")
    msg.sender to pool = router
    pool calls _beforeSwap(sender=router, ...)
    extension checks allowedSwapper[P][router] == true  ✓
    swap executes, bob receives output tokens

Result:
  bob, a non-allowlisted user, successfully swaps against the curated pool.
  The allowlist guard is silently bypassed.
``` [6](#0-5) [7](#0-6) [2](#0-1)

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
