### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every unprivileged caller can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is a two-sided identity mismatch:

| Scenario | Who the admin intended to gate | Who the extension actually checks |
|---|---|---|
| Router allowlisted | Specific trusted users | Router address → **any user passes** |
| Router not allowlisted | Specific trusted users | Router address → **allowlisted users blocked** |

The analog to the MiMC hash collision is exact: just as two distinct byte strings collapse to the same hash, two distinct callers (the real user and the router) collapse to the same identity at the allowlist check — the router address — causing the guard to either admit everyone or block legitimate users.

---

### Impact Explanation

**Critical path — allowlist bypass**: A pool admin who allowlists the router (the only way to let any allowlisted user swap through the router) simultaneously grants every non-allowlisted address the ability to swap in the restricted pool. An attacker calls `MetricOmmSimpleRouter.exactInputSingle` with the target pool; the extension sees `sender = router`, which is allowlisted, and passes. The attacker executes a swap that the pool's access policy was designed to prevent.

**Secondary path — broken functionality**: If the admin does not allowlist the router, allowlisted users cannot use the router at all, making the primary periphery entry point unusable for any allowlisted pool.

Both paths are direct consequences of the same root cause and are reachable by any unprivileged caller.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- No special role, token balance, or prior state is required to call `exactInputSingle`.
- The bypass is deterministic: every call through the router presents the router address to the extension.
- Pool admins have no on-chain mechanism to distinguish "router called on behalf of user X" from "router called on behalf of attacker Y" because the router does not forward the originating user's address as `sender`.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the **economically relevant actor**. Two options:

1. **Check `recipient` instead of `sender`** if the intent is to gate who receives tokens.
2. **Require the router to forward the originating user** — add a `payer` or `originator` field to `extensionData` and have the extension decode and verify it, with the router signing or encoding `msg.sender` before the pool call.

Option 2 is more robust because it preserves the router's role as a payment intermediary while still gating the real user identity.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, address(router), true)
   — required so that any allowlisted user can swap through the router.
3. Attacker (address NOT in allowedSwapper) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool:          <target pool>,
           recipient:     attacker,
           zeroForOne:    true,
           amountIn:      X,
           ...
       }))
4. Pool.swap is entered with msg.sender = router.
5. _beforeSwap passes sender = router to SwapAllowlistExtension.
6. Extension evaluates: allowedSwapper[pool][router] == true → no revert.
7. Swap executes. Attacker receives tokens from a pool that was supposed
   to be restricted to allowlisted addresses only.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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
