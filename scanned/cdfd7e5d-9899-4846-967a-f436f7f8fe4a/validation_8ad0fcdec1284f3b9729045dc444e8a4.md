### Title
`SwapAllowlistExtension` checks the router's address instead of the real user's address, allowing any unprivileged caller to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the original user. If the pool admin allowlists the router (the only way to let legitimate users reach the pool through the router), every unprivileged user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

Every public entry point in `MetricOmmSimpleRouter` calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user, allowlisted or not, can bypass the gate by routing through the router |

The extension has no mechanism to recover the original user's identity from the router call, so the two cases are indistinguishable at the extension layer.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-internal actors) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` targeting that pool. The unauthorized user executes real swaps, receives real token output, and the pool's LP funds settle the trade at oracle price — the same economic outcome as a legitimate allowlisted swap. There is no revert, no slippage penalty, and no on-chain record distinguishing the bypass from an authorized trade.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, permissionless periphery contract.
- Any user can call it with any pool address and any `extensionData`.
- The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration step for any pool that intends to support router-based trading for its allowlisted users.
- No privileged access, no special token, and no admin cooperation is required from the attacker.

---

### Recommendation

The extension must gate the **original user**, not the immediate caller of the pool. Two sound approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires the extension to trust the router as an authenticated forwarder, which must itself be allowlisted and verified.

2. **Check `sender` only for direct pool calls; require a signed or encoded user identity for router calls**: The extension can inspect whether `sender` is a known router and, if so, decode the real user from `extensionData`, reverting if the decoded user is not allowlisted.

The simplest safe fix is to remove the router from the allowlist and require all allowlisted users to call the pool directly, accepting that the router is incompatible with the allowlist model until the extension is updated to forward user identity.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only intended swapper
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack (executed by bob, who is NOT allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      <curated pool>,
      recipient: bob,
      zeroForOne: true,
      amountIn:  X,
      ...
  })

  Router calls pool.swap(bob_recipient, true, X, ..., extensionData)
    → msg.sender to pool = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → PASSES
    → swap executes, bob receives token output

Result:
  bob, a non-allowlisted address, completes a real swap in a curated pool.
  alice's allowlist entry is irrelevant; the router entry is the effective gate.
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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
