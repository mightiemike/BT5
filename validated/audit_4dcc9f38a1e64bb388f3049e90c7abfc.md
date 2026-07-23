### Title
`SwapAllowlistExtension` Gates Router Address Instead of End-User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router to enable router-mediated swaps, every unpermissioned user can bypass the allowlist by calling through the router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called swap() — the router when routed
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without forwarding the original `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

So the pool sees `msg.sender = router` and passes `sender = router` to the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Do** allowlist the router | Every non-allowlisted user can bypass the gate via the router |

The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` with `msg.sender = router`.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd market makers, whitelisted integrators, or compliance-gated participants) loses that restriction entirely for any user who routes through the public `MetricOmmSimpleRouter`. Unauthorized users can execute swaps at oracle-derived prices against LP funds, violating the pool's access-control invariant and potentially draining LP principal through adversarial oracle-priced trades that the allowlist was intended to prevent.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap interface for the protocol. Any pool that uses `SwapAllowlistExtension` and needs to support router-mediated swaps (the normal user path) must allowlist the router, at which point the bypass is unconditional and requires no special privileges — any EOA or contract can call `exactInputSingle`.

---

### Recommendation

Pass the original end-user identity through the hook rather than the immediate `msg.sender`. Two concrete approaches:

1. **Preferred — pass originator in `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. The pool already forwards `extensionData` unchanged to every hook.

2. **Alternative — add an originator field to the swap interface**: Extend `IMetricOmmExtensions.beforeSwap` with an explicit `originator` parameter that the pool populates from a trusted periphery context (e.g., transient storage set by the router before calling `swap`).

Until fixed, pools that require strict per-user swap gating should not use `SwapAllowlistExtension` in combination with `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)   // only Alice allowed
  - Admin calls setAllowedToSwap(pool, router, true)  // router must be allowed for UX

Attack:
  - Eve (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Eve's swap executes against LP funds at oracle price
  - Allowlist is fully bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
