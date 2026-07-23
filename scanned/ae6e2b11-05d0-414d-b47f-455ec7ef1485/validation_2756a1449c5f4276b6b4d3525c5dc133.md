### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual swapper, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router (a natural step to enable router-based swaps), every unprivileged address can bypass the per-user allowlist and trade on a curated pool.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` is the pool; `sender` is whoever called `pool.swap()`. [1](#0-0) 

The pool's `swap` function passes `msg.sender` (the direct caller) as `sender` to the extension: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value verbatim: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the call chain is:

```
user → router.exactInputSingle() → pool.swap(msg.sender=router) → extension.beforeSwap(sender=router)
``` [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual user's address is available as the `recipient` parameter (second argument, currently unnamed and silently discarded by the extension).

This creates two broken states:

1. **Router not allowlisted**: Individually allowlisted users cannot swap through the router at all, breaking the primary user-facing path.
2. **Router allowlisted**: Every address — including those explicitly excluded from the allowlist — can bypass the curation gate by routing through `MetricOmmSimpleRouter`.

The admin setter stores entries keyed by the user address: [5](#0-4) 

But the hook reads the key from `sender` (the router), making those per-user entries unreachable from the router path.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC'd users, specific market makers). If the pool admin allowlists the router to enable router-based swaps, the curation is silently voided: any address can call `router.exactInputSingle()` and trade on the pool. Non-curated users can extract LP value through arbitrage at the oracle price, causing direct loss of LP principal. The allowlist invariant — that only approved addresses may swap — is completely broken on the router path.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who configures `SwapAllowlistExtension` and also wants users to use the router will naturally allowlist the router address. The misconfiguration is non-obvious: the admin sets per-user entries via `setAllowedToSwap(pool, user, true)` and separately allowlists the router, not realizing the router entry overrides all per-user restrictions. The wrong-actor binding is invisible in the admin API and undocumented in the extension. Likelihood is medium.

---

### Recommendation

Replace the `sender` check with `recipient` (the second parameter) in `beforeSwap`, since `recipient` is the address the user specifies to receive output tokens and is the correct economic actor in single-hop swaps:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

For multi-hop paths where `recipient` may be an intermediate contract, require the actual user address to be passed via `extensionData` and verified by the extension, so the guard is always bound to the true economic actor regardless of routing depth.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Eve (not individually allowlisted) calls `router.exactInputSingle({pool: pool, recipient: eve, ...})`.
4. Router calls `pool.swap(recipient=eve, ...)` with `msg.sender = router`.
5. Pool calls `extension.beforeSwap(sender=router, recipient=eve, ...)`.
6. Extension checks `allowedSwapper[pool][router]` = `true` → passes.
7. Eve's swap executes on the curated pool, bypassing the individual allowlist entirely.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
