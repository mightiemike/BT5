### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router, not the end user. If the pool admin allowlists the router (the only way to enable router-based swaps), every unprivileged user can bypass the allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
```

At this point `msg.sender` inside the pool is the **router address**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`.

Two broken outcomes follow:

1. **Allowlist bypass**: If the pool admin allowlists the router (the natural configuration to permit router-based swaps), every user — including those explicitly excluded from the allowlist — can swap by calling the router. The check `allowedSwapper[pool][router] == true` passes for all of them.

2. **Broken router access for legitimate users**: If the pool admin does not allowlist the router, allowlisted EOAs cannot use the router at all, even though they are individually permitted. The only path that works is calling `pool.swap()` directly.

The `DepositAllowlistExtension` does not share this flaw because it gates the `owner` argument (the position owner), which the adder passes through unchanged regardless of who the payer is.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd participants, institutional counterparties, or protocol-internal actors) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The allowlist — the sole on-chain access-control mechanism for swaps — provides no protection once the router is allowlisted. Unauthorized users can execute swaps and receive pool output tokens, violating the pool admin's intended access policy and potentially causing direct economic harm to LPs who deposited under the assumption that only approved counterparties would trade.

---

### Likelihood Explanation

The router is a public, permissionless periphery contract. For any pool that intends to support router-based swaps (the standard UX path), the pool admin must allowlist the router. Once that is done, the bypass is trivially reachable by any EOA with no special privileges, no flash loan, and no token hook — a single call to `exactInputSingle` suffices. The only pools not affected are those that intentionally block the router and require direct `pool.swap()` calls, which is an unusual and undocumented restriction.

---

### Recommendation

The extension must check the **economic initiator**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` as a proxy for the end user** (weaker, not recommended): The recipient is caller-controlled and can be set to any address.

3. **Dedicated router allowlist**: Allowlist the router separately and require the router to attest the real sender via a signed payload or a separate registry, so the extension can verify the end user's identity even when the direct caller is the router.

The cleanest fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when `sender` is a known router address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only `alice` and the router:
      swapExtension.setAllowedToSwap(pool, alice, true)
      swapExtension.setAllowedToSwap(pool, address(router), true)
  - `charlie` is NOT allowlisted

Attack:
  charlie → MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
           → pool.swap(recipient=charlie, ...)   [msg.sender = router]
           → _beforeSwap(sender=router, ...)
           → SwapAllowlistExtension.beforeSwap(sender=router, ...)
           → allowedSwapper[pool][router] == true  ✓  (passes)
           → charlie receives pool output tokens

Result: charlie, who is not on the allowlist, successfully swaps in a restricted pool.
```

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2)

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
