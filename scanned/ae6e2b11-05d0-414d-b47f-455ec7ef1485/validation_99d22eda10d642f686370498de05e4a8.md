### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the original user. This creates an irreconcilable dilemma: either allowlisted users cannot use the router at all, or the pool admin must allowlist the router itself — which silently opens the pool to every user, nullifying the individual allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level. The original user's address is stored only in transient storage for the payment callback — it is never forwarded to the extension: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Consequence — two mutually exclusive broken states:**

| Pool admin action | Result |
|---|---|
| Allowlists individual users (alice, bob) | Alice/Bob are blocked when using the router; the extension sees the router address, not alice/bob |
| Allowlists the router to fix the above | Every user on the network can swap through the router, bypassing the individual allowlist entirely |

There is no configuration that simultaneously (a) allows allowlisted users to use the router and (b) blocks non-allowlisted users from using the router.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified market makers, institutional partners, or to protect LPs from adversarial flow) loses that protection the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and the extension will pass because it sees `allowedSwapper[pool][router] = true`. LP principal is exposed to unintended counterparties — a direct, fund-impacting consequence of the wrong-actor binding.

---

### Likelihood Explanation

The pool admin must allowlist the router for the bypass to be reachable. However, this is the natural and expected action when the admin wants allowlisted users to be able to use the standard periphery. The admin has no way to achieve the intended goal (allow alice through the router, block eve through the router) with the current design, so they are pushed toward the insecure configuration. The trigger is a valid, semi-trusted admin action with no malicious intent required.

---

### Recommendation

The extension must check the economically relevant actor — the original user — not the intermediary router. Two viable approaches:

1. **Router encodes the original user in `extensionData`**: The router appends `msg.sender` to the `extensionData` it forwards, and the extension decodes and checks that address when `sender` is a known periphery contract.
2. **Pool passes original user separately**: Add an `originator` field to the swap path so extensions can always see the end user regardless of routing depth.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position beneficiary), which the operator pattern cannot fake for the depositor's own benefit. The swap extension should adopt an equivalent canonical identity.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured on beforeSwap.
2. Admin allowlists alice:   setAllowedToSwap(pool, alice, true)
3. Admin allowlists router:  setAllowedToSwap(pool, router, true)
   (necessary step so alice can use the standard periphery)
4. Eve (not allowlisted) calls:
     MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — msg.sender at pool = router.
6. _beforeSwap passes sender = router to SwapAllowlistExtension.
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes.
8. Eve's swap executes against pool liquidity.
   allowlist is fully bypassed with zero privileged access.
``` [5](#0-4) [6](#0-5) [1](#0-0)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
