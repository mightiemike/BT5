### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which the pool sets to `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router address to enable router-mediated swaps for their curated users inadvertently opens the allowlist to every user who calls through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly without forwarding the original caller: [4](#0-3) 

So when a user swaps through the router, the value that reaches the extension's `sender` parameter is the **router's address**, not the user's address. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This creates an irresolvable configuration dilemma for any pool admin who wants to allow their allowlisted users to also use the router (the protocol's primary public entry point):

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot swap through the router at all |
| Router **allowlisted** | Every user on the network can bypass the allowlist via the router |

There is no configuration that achieves the intended semantics: "only allowlisted users may swap, whether directly or through the router."

---

### Impact Explanation

When the router is allowlisted (the natural choice for any pool that wants to support the standard periphery), the `SwapAllowlistExtension` is completely ineffective. Any address can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` against the pool and the allowlist check passes because `allowedSwapper[pool][router] == true`. The pool's curation policy is silently voided, and funds flow through a pool that was intended to be restricted.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the protocol's primary public swap interface. A pool admin who deploys a curated pool and wants their allowlisted users to be able to use the standard router will allowlist the router address. This is the expected operational path, not an edge case. The bypass is therefore reachable by any unprivileged user on any curated pool whose admin has taken the natural step of enabling router access.

---

### Recommendation

The extension must gate the **original end-user**, not the intermediary contract. Two sound approaches:

1. **Pass the real caller through `extensionData`**: Have the router encode `msg.sender` into the `extensionData` it forwards, and have `SwapAllowlistExtension` decode and verify it (with a check that `msg.sender` of the extension call is a trusted pool/router pair).

2. **Check `sender` only when it is not a known router**: The factory can maintain a registry of trusted routers; the extension skips the `sender` check and instead reads the payer address from a standardized field in `extensionData` when `sender` is a registered router.

Either way, the invariant must be: the address checked against the allowlist is the address that economically initiates and pays for the swap, regardless of which supported periphery contract relays the call.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; admin allowlists Alice (0xAlice).
2. Admin also calls setAllowedToSwap(pool, router, true) so that Alice
   (and other allowlisted users) can swap through MetricOmmSimpleRouter.
3. Bob (0xBob, not allowlisted) calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: 0xBob,
       ...
     })
4. Router calls pool.swap(...); pool's msg.sender = router.
5. _beforeSwap(sender=router, ...) → extension checks allowedSwapper[pool][router] == true → passes.
6. Bob's swap executes successfully despite not being on the allowlist.
```

The check that should have blocked Bob — `allowedSwapper[pool][0xBob]` — is never evaluated. The guard is structurally bypassed by routing through the allowlisted router. [5](#0-4) [6](#0-5) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
