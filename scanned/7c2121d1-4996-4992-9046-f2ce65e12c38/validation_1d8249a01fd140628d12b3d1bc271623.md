### Title
`SwapAllowlistExtension` checks router address as swapper, allowing any user to bypass the allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. If the pool admin allowlists the router address (the only way to let allowlisted users use the router), every unprivileged user can bypass the allowlist by routing through the same router.

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the router address when the call originates from the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

This creates an inescapable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **If the router is NOT allowlisted**: every allowlisted user who tries to use the router is blocked, breaking the standard swap UX.
- **If the router IS allowlisted** (the only way to restore router access for legitimate users): the check degenerates to `allowedSwapper[pool][router]`, which is `true` for every caller regardless of their own allowlist status. Any unprivileged user can call `exactInputSingle` on the router and the extension passes.

The actual user identity (`msg.sender` of the router call) is stored only in the router's transient callback context for payment purposes and is never forwarded to the pool or the extension: [6](#0-5) 

### Impact Explanation

A curated pool whose admin configured `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or whitelisted market makers) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The disallowed user receives output tokens from the pool and pays input tokens through the router callback — a complete, settled swap — with no revert. This breaks the core allowlist invariant and constitutes a direct policy bypass with fund-flow consequences: liquidity intended only for approved counterparties is consumed by unapproved actors.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly documented swap entry point in `metric-periphery`. Any user who reads the periphery interface will naturally use it. The bypass requires no special knowledge, no privileged role, and no unusual token behavior — only calling the standard router with a pool that has `SwapAllowlistExtension` configured and the router allowlisted. The pool admin is semi-trusted and has a legitimate reason to allowlist the router (to let approved users use it), making the misconfiguration a natural operational outcome rather than a malicious setup assumption.

### Recommendation

The extension must gate the **economically responsible actor**, not the intermediary. Two sound approaches:

1. **Forward the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `sender` only when it is not a known router; otherwise check a caller field from `extensionData`**: The extension reads a verified caller address from the extension payload when `sender` is a recognized router address.

The simplest correct fix is for the extension to check `sender` against the allowlist and for the router to pass the real user address as `sender` — but `sender` is set by the pool to `msg.sender`, so the router cannot inject it. The cleanest solution is to encode the real caller in `extensionData` and have the extension decode and verify it when `sender` is a known intermediary.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)` — extension checks `allowedSwapper[pool][router]` → `true`.
7. Swap executes. Bob receives output tokens. The allowlist is bypassed.

Direct call check (for comparison): if Bob calls `pool.swap(...)` directly, the extension checks `allowedSwapper[pool][bob]` → `false` → reverts `NotAllowedToSwap`. The bypass is exclusive to the router path. [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
