### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap` function sets `sender = msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router address (the natural fix to let allowlisted users use the supported periphery) inadvertently opens the allowlist to every user who routes through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**What `sender` actually is**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

`msg.sender` inside `pool.swap` is therefore the **router's address**, not the end user. The extension receives `sender = router` and checks `allowedSwapper[pool][router]`.

**The two broken states this creates**

| Pool admin configuration | Effect |
|---|---|
| Allowlist specific users only (not the router) | Allowlisted users cannot use the router; their swaps revert with `NotAllowedToSwap` even though they are explicitly permitted. Core periphery path is broken for them. |
| Allowlist the router to fix the above | Every user — including non-allowlisted ones — can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The curation policy is completely defeated. |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional partners) is fully bypassed. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the restricted pool and execute swaps that the allowlist was designed to block. This exposes LP funds to unauthorized counterparties and violates the pool's curation invariant, constituting a direct broken-core-functionality and policy-bypass impact.

---

### Likelihood Explanation

The router is the primary supported periphery swap path. A pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to be able to use the router will naturally allowlist the router address — this is the only way to make the router work for those users. The bypass is therefore triggered by a routine, expected configuration step, not an exotic edge case. Any pool using this extension with the router is affected.

---

### Recommendation

Pass the actual end-user identity through the hook rather than the immediate `msg.sender` of the pool call. Two concrete options:

1. **Preferred — thread the original caller through the router.** Have the router encode the real user address in `extensionData` and have the extension decode it. This requires a convention between the router and the extension.

2. **Simpler — check `recipient` instead of `sender` for the swap allowlist.** The `recipient` is the address that receives output tokens and is set by the user, not the router. However, `recipient` can also be a third-party address, so this is only correct if the pool's curation intent is to gate who receives output rather than who initiates the trade.

3. **Structural fix.** Add a dedicated `originalCaller` field to the `beforeSwap` hook signature so the pool can propagate the true initiator independently of the routing layer.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
        pool    = restrictedPool,
        tokenIn = token0,
        ...
    )
  - pool.swap(msg.sender=router, ...) is called
  - _beforeSwap(sender=router, ...) is dispatched
  - SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully despite not being allowlisted
```

The allowlist is completely bypassed. Bob trades on a pool that was supposed to be restricted to alice only. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
