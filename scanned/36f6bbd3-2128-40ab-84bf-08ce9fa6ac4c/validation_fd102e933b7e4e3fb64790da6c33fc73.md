### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any user to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to enable router-mediated swaps, every unpermissioned user can bypass the allowlist by calling the router instead of the pool directly.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value received from the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point the pool's `msg.sender` is the **router address**, so the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The originating user's address is never visible to the extension.

This creates an irreconcilable dilemma for any pool admin who wants to run a curated pool with router support:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — core swap path broken |
| **Allowlist the router** | Every user on-chain can bypass the allowlist by routing through the router |

The second branch is the exploitable path: once the router is allowlisted (the natural choice for a production pool), the allowlist provides zero protection.

The same actor-binding flaw applies to the multi-hop `exactInput` path (all hops call `pool.swap` from the router) and the recursive `exactOutput` path. [5](#0-4) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., KYC'd users, protocol-owned addresses, or whitelisted market makers). Once the router is allowlisted — which is the only way to let legitimate users trade via the standard periphery — the allowlist is completely defeated. Any unpermissioned address can call `exactInputSingle` or `exactInput` on the router and execute swaps against the pool's LP liquidity. LP funds are directly at risk because the pool will execute economically real swaps for actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user with tokens can call the public router. The only precondition is that the pool admin has allowlisted the router, which is the expected operational setup for any pool that intends to support the standard periphery. The router is a public, permissionless contract, so the bypass is reachable by any on-chain actor the moment the router is added to the allowlist.

---

### Recommendation

The extension must gate the **originating user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original `msg.sender` through the router as part of `extensionData`** and have the extension decode and verify it, combined with a router-signed attestation or a trusted-forwarder pattern so the extension can authenticate the claim.

2. **Move the allowlist check into the router itself** before calling the pool, and remove the extension-level check for router-mediated paths — accepting that direct pool calls bypass the router check and must be handled separately (e.g., by also checking at the pool level with a different mechanism).

The simplest correct fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension trusts the router address (verified via `msg.sender` being the pool and the pool's known router) to extract the real user identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router support
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    })
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes; attacker receives output tokens
  - allowedSwapper[pool][attacker] was never checked
```

The attacker successfully swaps on a pool that was configured to exclude them, with direct loss of LP value to the pool's liquidity providers.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
