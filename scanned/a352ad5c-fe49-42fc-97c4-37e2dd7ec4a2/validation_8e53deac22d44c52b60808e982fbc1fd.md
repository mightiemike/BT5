### Title
SwapAllowlistExtension gates the router address instead of the actual user on router-mediated swaps, enabling full allowlist bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the user. If the pool admin allowlists the router — a natural step to enable router-mediated swaps for allowlisted users — every unprivileged user can bypass the curated-pool gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. The pool sets that argument to its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls the pool **directly**, `sender = user`. When a user calls through `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`, so `sender = router`:

```solidity
// exactInputSingle — msg.sender of pool.swap is the router
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [3](#0-2) 

The same holds for every hop in `exactInput` and every recursive step in `exactOutput`:

```solidity
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY,
    i == 0 ? msg.sender : address(this), params.tokens[i]);
IMetricOmmPoolActions(pool).swap(
    i == last ? params.recipient : address(this), ...
);
``` [4](#0-3) 

The extension therefore sees `sender = router` for every router-mediated swap, regardless of who the actual user is.

**Bypass path**: A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, `allowedSwapper[pool][router]` returns `true` for every caller, so any unprivileged user can swap on the curated pool simply by going through the router.

**Broken-functionality path**: If the admin does *not* allowlist the router, every allowlisted user is silently blocked from using the standard periphery path, making the curated pool unusable through the supported router interface.

This is the direct analog of the ERC20/ERC777 bug: in that case `preventLocking = true` was applied to ERC20 transfer paths when it should only apply to ERC777 send/mint paths. Here, the allowlist check is applied to the immediate caller of the pool (the router) rather than the actual economic actor (the user), for the router-mediated code path.

---

### Impact Explanation

Once the router is allowlisted (the only way to let allowlisted users use the router), **any** address can bypass the curated-pool gate. Unauthorized users gain full swap access, defeating KYC/institutional gating, and can drain the pool's liquidity at oracle-derived prices. This is a direct loss of LP principal and a complete failure of the configured access-control invariant.

---

### Likelihood Explanation

The trigger is a single, operationally motivated admin action: allowlisting the router so that allowlisted users can use the standard periphery path. The admin has no on-chain signal that this opens the pool to everyone. The bypass is then reachable by any unprivileged user with zero additional privilege. Likelihood is **medium** (requires the admin to take the natural remediation step) with **high** impact once triggered.

---

### Recommendation

The extension must check the actual economic actor, not the immediate pool caller. Two options:

1. **Router-forwarded identity**: Have the router ABI-encode the real user address into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.
2. **Recipient-based check**: For swap allowlists, gate on `recipient` (the address that receives tokens) rather than `sender`, since the recipient is always the intended beneficiary and cannot be spoofed by an intermediary.

Option 1 is more general and preserves the sender-gating semantics; option 2 is simpler but changes the gating axis.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; pool admin allowlists alice:
       extension.setAllowedToSwap(pool, alice, true)

2. Pool admin also allowlists the router so alice can use it:
       extension.setAllowedToSwap(pool, router, true)

3. Unprivileged user charlie (NOT allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: charlie, ...})

4. Router calls pool.swap(...); pool calls extension.beforeSwap(router, ...)
       allowedSwapper[pool][router] == true  →  check passes

5. Charlie's swap executes on the curated pool — allowlist fully bypassed.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
