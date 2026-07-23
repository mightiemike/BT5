After thorough analysis of the codebase, I have identified a valid analog.

---

### Title
Swap Allowlist Guard Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the pool to **all** users, completely defeating the allowlist.

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

So `sender` arriving at the extension is the **router's address**, not the end user's address. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irresolvable dilemma for any pool admin who deploys a pool with `SwapAllowlistExtension`:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — legitimate allowlisted users cannot use the router |
| Router **allowlisted** | Every address on-chain can bypass the individual allowlist by routing through the router |

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd market makers, whitelisted institutions, or curated LPs) cannot enforce that restriction for any user who routes through `MetricOmmSimpleRouter`. If the router is allowlisted (the only way to enable router-mediated swaps), any unprivileged address can execute swaps on the restricted pool. This breaks the core allowlist invariant and constitutes a direct policy bypass with fund-impacting consequences: unauthorized parties can drain LP value through unrestricted trading on a pool that was designed to be curated.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary production swap interface. Pool admins who deploy allowlisted pools and want to support standard user flows will naturally allowlist the router, not realizing this collapses the per-user gate. The bypass requires no special privileges — any EOA can call `exactInputSingle` on the router.

### Recommendation

The `SwapAllowlistExtension` should gate on the **original end user**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original sender through extension data**: The router should include `msg.sender` (the end user) in `extensionData`, and the extension should decode and check that value. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the end user, though this breaks for multi-hop paths.

3. **Dedicated router-aware allowlist**: The extension could accept a secondary "original sender" field in `extensionData` and verify it is signed or attested by the router, preventing spoofing.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only Alice is allowed.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router allowlisted so Alice can use it.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient, ...) — msg.sender = router.
6. beforeSwap receives sender = router.
7. allowedSwapper[pool][router] == true → check passes.
8. Bob's swap executes on the restricted pool. ✓ bypass confirmed.
``` [5](#0-4) [4](#0-3) [1](#0-0)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```
