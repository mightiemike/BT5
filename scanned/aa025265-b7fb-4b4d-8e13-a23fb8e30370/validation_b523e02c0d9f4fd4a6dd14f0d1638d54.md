### Title
SwapAllowlistExtension Checks Router Address Instead of Real Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the user. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unprivileged user can bypass the allowlist by calling the router, defeating the access control entirely.

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of the pool call: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — in every router path the pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`. [5](#0-4) 

The allowlist mapping is keyed by pool and swapper: [6](#0-5) 

There are only two coherent configurations, both broken:

| Admin action | Effect |
|---|---|
| Allowlist specific users only | Those users cannot use the router (router not allowlisted → reverts) |
| Allowlist the router to enable router-mediated swaps | Every user on the network can bypass the allowlist via the router |

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) becomes fully open to any caller the moment the pool admin allowlists the router. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and swap against the restricted pool, extracting value from LPs who expected controlled access. The allowlist guard — the only mechanism preventing unauthorized swaps — is silently bypassed with no on-chain signal.

### Likelihood Explanation

The pool admin must allowlist the router for this to be exploitable. This is a natural and expected configuration step: any pool that wants to support the standard periphery router for its permitted users must allowlist the router address. The moment that step is taken, the allowlist is nullified for all router-mediated swaps. The trigger is a routine admin action, not an exotic attack setup.

### Recommendation

The extension must resolve the real user identity rather than the immediate caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router or a signed payload.
2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant actor is often the recipient; however this also has edge cases.
3. **Preferred — check both `sender` and `recipient` or require direct pool calls**: Document that router-mediated swaps are incompatible with `SwapAllowlistExtension` and enforce this at the extension level by reverting when `sender` is a known router, or require users to call the pool directly.

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is permitted.
3. Pool admin calls setAllowedToSwap(pool, router, true) — to let alice use the router.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(); pool passes msg.sender=router to _beforeSwap.
6. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
7. Bob's swap executes against the restricted pool. Alice's allowlist protection is void.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
