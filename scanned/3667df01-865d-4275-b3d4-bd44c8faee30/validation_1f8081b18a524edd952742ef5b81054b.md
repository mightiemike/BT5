### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool passes, which is always `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, that `sender` is the router's address, not the actual end-user. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to every user, completely defeating the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

From the pool's perspective `msg.sender` is the router, so `sender` delivered to the extension is the router's address. The router never injects the actual caller's identity into `extensionData`; the extension has no other channel to learn who the real user is.

This creates an irresolvable dilemma for any pool admin who configures a swap allowlist and also wants to support router-mediated swaps:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the network can swap via the router, bypassing the per-user allowlist entirely |

The router is a public, permissionless contract. Once it is allowlisted, `allowedSwapper[pool][router] == true` satisfies the guard for every caller regardless of their individual allowlist status.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is typically intended to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, specific market makers, or whitelisted protocols). If the router is allowlisted to support normal UX, any unprivileged address can execute swaps at oracle-anchored prices against LP capital that was deposited under the assumption of restricted access. This constitutes a direct loss of LP principal through unauthorized price-taking against restricted liquidity.

---

### Likelihood Explanation

High. Supporting the router is the standard UX path for end-users. A pool admin who deploys a swap-allowlisted pool and also wants users to be able to use the router will naturally allowlist the router. The documentation does not warn that doing so disables per-user access control. The bypass requires no special privilege, no malicious setup, and no non-standard token behavior — only a call to the public router.

---

### Recommendation

1. **Pass caller identity through `extensionData`**: The router should encode `msg.sender` into the `extensionData` it forwards to the pool. The `SwapAllowlistExtension` should decode and verify that address instead of (or in addition to) `sender`.

2. **Alternatively, check both `sender` and a decoded user field**: The extension can require that when `sender` is a known router, a verified user address is present in `extensionData` and that address is allowlisted.

3. **At minimum, document the limitation explicitly**: If the design intent is that the allowlist only gates direct pool calls, the `SwapAllowlistExtension` NatDoc must state that allowlisting any intermediary contract (router, aggregator, multicall) opens the pool to all callers of that intermediary.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as the `beforeSwap` extension.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin also calls `swapExtension.setAllowedToSwap(pool, router, true)` — to support router-mediated swaps for Alice.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — pool sees `msg.sender == router`.
6. Pool calls `_beforeSwap(router, ...)` — extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully against LP capital that was deposited under the assumption that only Alice could trade.

Direct call by Bob to `pool.swap(...)` would correctly revert with `NotAllowedToSwap` because `allowedSwapper[pool][bob] == false`. The router path silently bypasses this check. [3](#0-2) [1](#0-0) [5](#0-4)

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
