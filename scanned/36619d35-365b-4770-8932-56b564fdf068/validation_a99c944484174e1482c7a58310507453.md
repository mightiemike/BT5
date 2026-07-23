### Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any account to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so the extension checks the router's allowlist status rather than the originating user's. If the pool admin allowlists the router (the only way to enable router-mediated swaps for legitimate users), every unpermissioned account can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool passes `msg.sender` of `pool.swap()` as `sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

So `msg.sender` of `pool.swap()` is the router, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly, substituting the router for the user.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` must allowlist the router if they want any of their approved users to trade through the standard periphery. Once the router is allowlisted, the allowlist is effectively open to every account on-chain: any disallowed user calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the pool, the extension sees `sender = router`, finds it allowlisted, and permits the swap. The curated pool's access control is completely defeated. Disallowed users can drain liquidity, extract fees, or trade against LP positions they were never meant to access.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented swap entry point for end users. Any pool admin who wants their allowlisted users to use the router must add the router to the allowlist. This is the expected operational pattern, so the precondition (router is allowlisted) is the normal deployment state, not an edge case. Any unpermissioned account can exploit this with a single public call to the router.

---

### Recommendation

The extension must gate the economically relevant actor — the originating user — not the immediate caller of `pool.swap()`. Two options:

1. **Pass the original user through the router.** The router should forward the originating `msg.sender` as a verified parameter (e.g., via `extensionData` with a signature, or via a trusted forwarder pattern), and the extension should read that value instead of `sender`.

2. **Check `sender` only for direct pool calls; reject router-mediated calls on allowlisted pools.** The extension could require `sender == tx.origin` or require that `sender` is not a known router, though this is fragile.

The cleanest fix is option 1: the pool's `swap()` interface should carry the originating user identity in a way the extension can verify, rather than relying on the immediate `msg.sender`.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - Pool admin calls setAllowedToSwap(pool, router, true)   ← required for Alice to use the router
  - Bob is NOT in the allowlist

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → msg.sender of pool.swap() = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. ExtensionCalling encodes sender=router and calls SwapAllowlistExtension.beforeSwap
  5. Extension checks: allowedSwapper[pool][router] == true  → passes
  6. Swap executes. Bob has traded on a pool he was never permitted to access.

Direct call (for comparison, correctly blocked):
  1. Bob calls pool.swap(...) directly
  2. Pool calls _beforeSwap(sender=bob, ...)
  3. Extension checks: allowedSwapper[pool][bob] == false → reverts NotAllowedToSwap
```

The invariant broken is: `allowedSwapper[pool][sender]` must reflect the originating user, but when the router is the intermediary, `sender` is the router, not the user. The allowlist check passes for any user who routes through an allowlisted router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
