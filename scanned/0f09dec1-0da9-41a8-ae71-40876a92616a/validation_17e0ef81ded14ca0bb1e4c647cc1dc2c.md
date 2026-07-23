### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of that call, so the extension sees the router address — not the actual user. Any pool admin who allowlists the router to let legitimate users reach the pool simultaneously opens the gate for every non-allowlisted address to bypass the curated-pool restriction.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` parameter of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

**Step 2 — The extension checks `allowedSwapper[pool][sender]`.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key for the pool dimension and the forwarded `sender` argument as the swapper identity: [3](#0-2) 

**Step 3 — The router is `msg.sender` of the pool call, not the user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The router is the caller of the pool, so `msg.sender` inside `MetricOmmPool.swap` is the router address: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who allowlists the router so that legitimate users can reach the pool through the standard periphery path simultaneously grants every non-allowlisted address the ability to swap by routing through the same contract.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled addresses) loses that restriction entirely the moment the router is allowlisted. Any address — including those the pool admin explicitly excluded — can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity. This is a direct, complete bypass of the pool's access-control invariant with no fund-loss floor: the attacker trades at oracle-anchored prices, draining LP value at will.

---

### Likelihood Explanation

The router is the canonical user-facing swap entry point for the protocol. A pool admin who wants allowlisted users to benefit from slippage protection, multi-hop routing, or permit-based approvals must allowlist the router. The bypass is therefore a natural consequence of normal, expected pool configuration. No privileged access, no malicious setup, and no non-standard tokens are required — only a standard `exactInputSingle` call from any EOA.

---

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary contract. Two sound approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before forwarding it to the pool; the extension decodes and verifies that identity. This requires a coordinated change in the router and the extension.

2. **Check `sender` only for direct pool calls; require a signed proof for router calls:** The extension inspects whether `sender` is a known router and, if so, decodes a user-signed allowlist proof from `extensionData`.

The simplest safe default is to treat any unrecognized `sender` (i.e., not individually allowlisted) as blocked, and require the router to be explicitly excluded from the allowlist path — meaning the pool admin must choose between "router access for all" or "direct-only access for the allowlist."

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is the only allowed swapper.
3. Pool admin calls setAllowedToSwap(pool, router, true)  // Router allowlisted so Alice can use it.

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          <curated pool>,
           recipient:     bob,
           zeroForOne:    true,
           amountIn:      X,
           ...
       })

5. Router calls pool.swap(bob, true, X, ...).
   Inside pool.swap: msg.sender == router.
   _beforeSwap(router, bob, ...) is called.

6. SwapAllowlistExtension.beforeSwap receives sender = router.
   Check: allowedSwapper[pool][router] == true  ✓
   Extension returns selector — swap proceeds.

7. Bob receives token1 output. Allowlist is bypassed.
```

The check that should have blocked Bob — `allowedSwapper[pool][bob]` — is never evaluated. The extension evaluated `allowedSwapper[pool][router]` instead.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
