### Title
Swap Allowlist Bypass via Router — Wrong Actor Binding in `SwapAllowlistExtension.beforeSwap` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed on a curated pool), every unpermissioned user can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, the check passes for **every** caller of the router, regardless of whether that caller is individually permitted.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: only specific addresses are supposed to be able to trade. The bypass collapses that guarantee entirely. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` (or any other `exact*` entry point) and execute a swap on the restricted pool. The economic consequences are direct: unauthorized traders can extract value from the pool's oracle-anchored liquidity, front-run allowlisted participants, or drain bins that the pool admin intended to reserve for a closed set of counterparties. This is a direct loss of principal and fee revenue for LPs on curated pools.

---

### Likelihood Explanation

The router is a public, permissionless contract. No special role, privilege, or setup is required beyond calling a standard swap function. The only precondition is that the pool admin has allowlisted the router — a step they must take if they want any of their allowlisted users to be able to use the router at all. The dilemma is structural: allowlist the router and lose per-user gating; do not allowlist the router and force allowlisted users to call the pool directly. There is no configuration that achieves both goals simultaneously with the current implementation.

---

### Recommendation

The extension must recover the original end-user identity rather than accepting the router's address as the swapper. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it, then cross-checks that the pool's `sender` argument is a known trusted router. This requires the extension to maintain a registry of trusted routers per pool.

2. **Transient-storage forwarding**: The router writes the real user address into a transient storage slot before calling the pool. The extension reads that slot (via a shared interface) and uses it as the identity to check. The pool's reentrancy guard already uses transient storage for callback context, so the pattern is established.

Either approach must ensure the forwarded identity cannot be spoofed by an untrusted caller.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only permitted swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router
4. alice adds liquidity; pool now holds token0/token1.

Attack
──────
5. charlie (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          pool,
           recipient:     charlie,
           zeroForOne:    true,
           amountIn:      X,
           ...
       })

6. Router calls pool.swap(charlie, true, X, ...).
   Inside pool.swap: msg.sender == router.

7. Pool calls _beforeSwap(router, charlie, ...).
   Extension receives sender == router.
   Check: allowedSwapper[pool][router] == true  →  passes.

8. Swap executes. charlie receives token1 from the curated pool
   without ever being individually allowlisted.
```

The allowlist is fully bypassed. The pool admin's intent — restricting swaps to `alice` — is violated by a single public router call from any address.

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
