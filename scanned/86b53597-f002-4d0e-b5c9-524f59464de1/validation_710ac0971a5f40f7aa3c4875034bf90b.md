### Title
SwapAllowlistExtension gates the router contract address instead of the actual swapper, allowing any user to bypass per-user swap restrictions via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, `sender` equals the router contract address, not the originating user. A pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the pool to every user, because the extension cannot distinguish individual users behind the router.

---

### Finding Description

**Call path for a direct swap:**

```
user → pool.swap()
  msg.sender = user
  _beforeSwap(sender=user, ...)
  SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][user]  ✓
```

**Call path for a router-mediated swap:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
  router → pool.swap(params.recipient, ...)
    msg.sender = router
    _beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

The router never forwards the originating user's address to the pool. It stores the payer in transient storage for the callback only:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(params.recipient, ...);
```

The actual user (`msg.sender` of the router call) is never visible to the extension.

**Consequence of the structural mismatch:**

There is no configuration of `SwapAllowlistExtension` that simultaneously:
- Allows specific allowlisted users to swap through the router, AND
- Blocks non-allowlisted users from swapping through the router.

The only two options are:
1. Do not allowlist the router → allowlisted users cannot use the router at all.
2. Allowlist the router → every user can bypass the per-user allowlist via the router.

A pool admin who wants to support router-mediated swaps for their curated users will naturally allowlist the router, which opens the pool to all users.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled addresses). Once the pool admin allowlists the router to support the standard periphery flow, any unprivileged user can execute swaps on the curated pool by routing through `MetricOmmSimpleRouter`. The allowlist protection is fully bypassed for all router-mediated swaps, allowing unauthorized users to trade against LP funds at oracle-derived prices. This constitutes a broken core pool functionality with direct exposure of LP assets to unintended counterparties.

---

### Likelihood Explanation

Allowlisting the router is the natural and expected action for any pool admin who wants their curated pool to be usable through the standard periphery. The `MetricOmmSimpleRouter` is the documented swap entry point. A pool admin who does not allowlist the router renders the router unusable for their allowlisted users, which is a non-obvious and undocumented restriction. The bypass is therefore reachable on any curated pool that supports router-mediated swaps, which is the common deployment pattern.

---

### Recommendation

The extension must gate the originating user, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it. This requires the router to be trusted to supply the correct value, which is acceptable since the router is a known periphery contract.

2. **Check `recipient` instead of `sender` for router flows**: The router sets `recipient` to the user's intended output address. This is imperfect (recipient ≠ payer) but avoids the router-address problem.

3. **Structural fix**: Add a `trustedForwarder` mapping to the extension. When `sender` is a trusted forwarder (e.g., the router), decode the real user from `extensionData` and apply the allowlist check against that address.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    // natural action to support router-mediated swaps

Attack:
  attacker (not in allowedSwapper[pool]) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: attacker,
      ...
    })

  Router calls: pool.swap(attacker, zeroForOne, amount, limit, "", extensionData)
    msg.sender = router

  Pool calls: _beforeSwap(sender=router, ...)

  SwapAllowlistExtension.beforeSwap:
    allowedSwapper[pool][router] == true  ← passes
    swap executes against LP funds

Result:
  Attacker swaps on a curated pool without being individually allowlisted.
  The per-user allowlist is fully bypassed for all router-mediated swaps.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
```
