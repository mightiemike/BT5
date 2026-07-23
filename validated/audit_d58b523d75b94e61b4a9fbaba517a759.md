### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swap access by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool's `swap` call, so the extension checks the router's address against the allowlist instead of the actual user's address. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), every user — including non-allowlisted ones — can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` inside the extension is the pool; `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

From the pool's perspective `msg.sender` = router, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

This creates an irresolvable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use the router at all (broken flow) |
| Yes | **Any** user bypasses the allowlist by routing through the router |

The same identity-mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — every router entry point calls `pool.swap` with the router as `msg.sender`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional or KYC-gated pools) loses that restriction entirely once the router is allowlisted. Any unprivileged user can execute swaps on the restricted pool by calling the public `MetricOmmSimpleRouter`, draining or manipulating the pool in ways the admin explicitly intended to prevent. This is a direct bypass of an admin-configured access-control boundary reachable through a supported public periphery path.

---

### Likelihood Explanation

The bypass is triggered by any user who calls the public router against a pool that has the router allowlisted. The admin must allowlist the router — a natural and expected action when the pool is meant to be accessible through the standard periphery — making the precondition realistic in any production deployment that uses the router alongside the allowlist extension.

---

### Recommendation

The extension must check the original end-user's address, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the original sender through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, also verifying that `msg.sender` (the pool's caller) is a trusted router registered with the factory.

2. **Check `sender` only when the caller is not a trusted router**: The extension queries the factory for a registered router list and, when `sender` is a known router, falls back to a user address embedded in `extensionData`.

Either way, the allowlist must gate the economically relevant actor (the end user), not the intermediary contract.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Pool calls _beforeSwap(router, ...)
6. Extension checks allowedSwapper[pool][router] == true  → passes
7. Bob's swap executes on the restricted pool.
```

The allowlist check `allowedSwapper[pool][sender]` evaluates to `allowedSwapper[pool][router]` = `true`, so Bob's swap is indistinguishable from Alice's swap at the extension level. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
