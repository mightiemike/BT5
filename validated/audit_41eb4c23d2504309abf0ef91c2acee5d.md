### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the user. If the pool admin allowlists the router (the natural configuration for a pool that wants to support the standard periphery), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with its own `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that value as the first argument to every configured extension: [2](#0-1) 

**Step 2 — The router is the direct caller of `pool.swap()`.**

`MetricOmmSimpleRouter.exactInputSingle` and `exactInput` call `pool.swap()` directly. The pool therefore sees `msg.sender = router`: [3](#0-2) 

For multi-hop `exactInput`, every hop is also called directly by the router: [4](#0-3) 

**Step 3 — The extension checks the router address, not the user.**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [5](#0-4) 

**Two broken outcomes result:**

| Pool admin configuration | Observed behaviour |
|---|---|
| Allowlists the router address (to let users use the standard periphery) | `allowedSwapper[pool][router] = true` → **every user on the network passes the check**; the allowlist is completely ineffective |
| Allowlists individual user addresses | `allowedSwapper[pool][user] = true` but the extension sees `sender = router` → **allowlisted users cannot swap through the router**; they must call the pool directly |

Neither outcome matches the intended invariant: *"a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."*

---

### Impact Explanation

**Allowlist bypass (high):** A pool admin who allowlists the router — the natural action when deploying a pool intended for public use via the standard periphery — inadvertently grants every address on the network the right to swap. Any non-KYC'd or otherwise excluded user calls `exactInputSingle` on the router and the `beforeSwap` hook passes, because the hook sees the allowlisted router address, not the user's address. The curated pool's entire access-control guarantee is lost.

**Broken core functionality (medium):** A pool admin who allowlists individual user addresses instead finds that those users cannot use the router at all. The router is not allowlisted, so every router-mediated swap reverts with `NotAllowedToSwap`, forcing allowlisted users to interact with the pool contract directly — a broken UX that defeats the purpose of the periphery layer.

---

### Likelihood Explanation

The trigger is a valid, unprivileged public action (calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`) combined with a natural pool-admin configuration (allowlisting the router). No malicious setup is required. Any user who knows the pool has a `SwapAllowlistExtension` and that the router is allowlisted can exploit this immediately. The router is a canonical, publicly deployed contract, so the configuration is expected to appear in production.

---

### Recommendation

The extension must gate the **originating user**, not the direct caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the router.** The router already stores the real payer in transient storage (`_getPayer()`). Extend the pool's `swap` signature or the `extensionData` payload to carry the originating user address, and have the extension read it from there.

2. **Alternatively, check `recipient` instead of `sender` for the swap allowlist.** The `recipient` is the address that receives output tokens and is set by the user in `ExactInputSingleParams.recipient`. Gating on `recipient` is economically equivalent for single-hop swaps and is not spoofable by the router.

3. **Document the actor semantics clearly.** Until a structural fix is in place, the extension's NatSpec must warn that `sender` is the direct pool caller, not the originating EOA, so pool admins do not allowlist the router expecting per-user gating.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured (allowAll = false).
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow users to swap via the standard periphery.
3. Attacker (address not in allowedSwapper) calls:
     MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(); pool sees msg.sender = router.
5. _beforeSwap passes sender = router to SwapAllowlistExtension.beforeSwap.
6. Extension evaluates: allowedSwapper[pool][router] == true → passes.
7. Attacker's swap executes on the curated pool despite never being allowlisted.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
