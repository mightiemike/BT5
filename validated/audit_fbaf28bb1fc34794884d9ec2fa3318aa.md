### Title
`SwapAllowlistExtension` Allowlist Bypassed via Router: Any User Can Swap Against a Curated Pool - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the actual user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for their curated users), every unprivileged user can bypass the allowlist entirely by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool therefore sees `msg.sender = router`. The extension receives `sender = router` and evaluates `allowedSwapper[pool][router]`. The actual end-user's address is never visible to the extension.

A pool admin who wants to support router-mediated swaps for their allowlisted users must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, **every** caller of `router.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) passes the check, regardless of whether their own address is on the allowlist.

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional-only, or protocol-internal) that relies on `SwapAllowlistExtension` to restrict who may trade is fully open to any public user the moment the router is allowlisted. The attacker:

- Pays no fee beyond normal swap fees.
- Needs no special role or privilege.
- Receives the same oracle-priced output as any allowlisted trader.

Consequences include unauthorized extraction of LP value, front-running by non-allowlisted actors, and complete nullification of the pool's curation policy — all of which constitute direct loss of LP principal or broken core pool functionality.

---

### Likelihood Explanation

The scenario is not hypothetical. A pool admin who deploys a curated pool and wants their allowlisted users to be able to use the standard router **must** allowlist the router address. There is no other supported path: the router is the only public periphery swap entrypoint, and the extension has no mechanism to distinguish the router from an end-user. The misconfiguration is therefore the expected, natural configuration for any curated pool that also supports router access.

---

### Recommendation

The extension must gate the **economically responsible actor**, not the immediate pool caller. Two viable approaches:

1. **Originator forwarding**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and check that address when `sender` is a known router. This requires a trust relationship between the router and the extension.

2. **Separate allowlist for intermediaries**: Distinguish between "direct swapper allowlist" and "trusted intermediary allowlist". Only trusted intermediaries (router) are allowed to forward swaps on behalf of allowlisted users, and the extension must verify the forwarded identity from `extensionData`.

3. **Block router entirely on curated pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and provide a separate router variant that forwards the originating user address in `extensionData` for extension consumption.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...) with msg.sender = router
  - pool calls extension.beforeSwap(router, ...) with msg.sender = pool
  - extension checks allowedSwapper[pool][router] == true  → passes
  - swap executes; attacker receives output tokens

Result:
  - attacker, who is NOT on the allowlist, successfully swaps against the curated pool.
  - The SwapAllowlistExtension guard is silently bypassed.
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
