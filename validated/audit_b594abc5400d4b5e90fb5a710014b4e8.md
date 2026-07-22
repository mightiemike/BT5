The call chain is fully traceable. Here is the complete analysis:

---

## Call Chain Trace

**Direct swap path:**
```
user → pool.swap(msg.sender=user) → _beforeSwap(sender=user) → extension.beforeSwap(sender=user)
→ allowedSwapper[pool][user]  ✓ correct identity
```

**Router swap path:**
```
user → router.exactInputSingle() → pool.swap(msg.sender=router) → _beforeSwap(sender=router)
→ extension.beforeSwap(sender=router) → allowedSwapper[pool][router]  ✗ wrong identity
```

The identity checked by the hook is the **immediate caller of `pool.swap()`**, not the originating user.

---

## Code Evidence

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` (router address) and forwards it to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap()` directly with no user-identity forwarding: [4](#0-3) 

---

## The Dilemma This Creates

A pool admin using `SwapAllowlistExtension` faces an inescapable choice:

| Admin action | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| Do NOT allowlist router | Blocked (broken for legitimate users) | Blocked |
| Allowlist router | Allowed | **Also allowed — bypass** |

There is no configuration that allows allowlisted users to use the official router while blocking non-allowlisted users. Allowlisting the router — the only way to enable router-mediated swaps — grants the router blanket permission, and since the router is a public permissionless contract, any user can exploit this.

---

## Verdict

### Title
Router-Mediated Swaps Bypass Per-User Allowlist in `SwapAllowlistExtension` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When the official `MetricOmmSimpleRouter` is used, `sender` is the router address, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for their curated pool inadvertently grants all users unrestricted swap access, defeating the allowlist entirely.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`, which forwards it unchanged to the extension. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` — keyed on the router address, not the originating user. Because `MetricOmmSimpleRouter` is a public, permissionless contract with no access control of its own, allowlisting it is equivalent to setting `allowAllSwappers[pool] = true`. The admin has no mechanism to distinguish which user is behind a router call.

### Impact Explanation
A curated pool's allowlist is completely bypassed for any user who routes through the official periphery. Non-KYC'd, non-institutional, or otherwise excluded users can trade freely on pools designed to restrict access. This is a broken core functionality / curation failure: the pool cannot enforce its intended access policy through the supported public entrypoint.

### Likelihood Explanation
Any pool admin who deploys `SwapAllowlistExtension` and also wants their allowlisted users to use the router will naturally allowlist the router. The bypass is then available to every user on-chain with no special knowledge or setup required.

### Recommendation
The extension must receive the **originating user** identity, not the immediate caller. Options:
1. The pool should forward an additional `origin` parameter (e.g., `tx.origin` or a user-supplied signed identity) to hooks.
2. The router should pass the originating user through `extensionData`, and the extension should decode and verify it (with a signature or trusted-router attestation).
3. The allowlist should gate the router separately and require the router to enforce its own per-user access control before calling the pool.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Non-allowlisted attacker calls `router.exactInputSingle({pool: pool, ...})`.
4. Pool calls `_beforeSwap(sender=router)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. Attacker successfully trades on a pool they were never allowlisted for.

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
