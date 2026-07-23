### Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool always sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the original user. A pool admin who allowlists the router to enable legitimate router-mediated swaps simultaneously grants every unprivileged user the ability to bypass the allowlist by calling the same router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first positional argument in the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value just described: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap` is the **router address**, so `sender` delivered to the extension is the router, not the original user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to be able to use the standard router UX must call `setAllowedToSwap(pool, router, true)`. The moment that entry is set, every caller of `router.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) passes the check, because the extension sees only the router address and that address is now approved. The original user's address is never consulted.

The same bypass is available through the multi-hop `exactInput` path, where intermediate hops use `address(this)` (the router) as payer: [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd institutions, protocol-owned addresses, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker receives the same oracle-priced execution as an allowlisted trader, draining pool liquidity or extracting favorable rates that were reserved for approved parties. This is a direct loss of LP principal and a broken core pool invariant (access control).

---

### Likelihood Explanation

The trigger is unprivileged and requires no special setup beyond the pool admin having taken the natural operational step of allowlisting the router. Any user who discovers the pool is accessible via the router can exploit it immediately. The router is a public, documented periphery contract, so the attack surface is fully reachable without any privileged action by the attacker.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass original user via `extensionData`**: Have the router encode `msg.sender` (the original user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it. The extension must also verify that the `sender` (pool's `msg.sender`) is a known router to prevent spoofing.

2. **Check `sender` only for direct pool calls; require `extensionData` proof for router calls**: The extension inspects whether `sender` is a known router; if so, it decodes the original user from `extensionData` and checks that address against the allowlist instead.

The current design where `sender` is always the immediate caller of `pool.swap` makes it structurally impossible to simultaneously allow router-mediated swaps for allowlisted users and block non-allowlisted users from using the same router.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin allowlists Alice:
       setAllowedToSwap(pool, alice, true)
3. Alice uses the router → blocked (router not allowlisted).
4. Pool admin allowlists the router to fix Alice's UX:
       setAllowedToSwap(pool, router, true)
5. Bob (never allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
6. Router calls pool.swap(...); pool passes msg.sender=router to _beforeSwap.
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes on the restricted pool.
   Bob was never added to the allowlist; the guard is fully bypassed.
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
