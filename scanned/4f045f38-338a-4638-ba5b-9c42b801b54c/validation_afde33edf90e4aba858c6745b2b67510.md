### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes `msg.sender` of the `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address — not the actual user's address — against the allowlist. Any user can therefore bypass a per-user swap allowlist by calling the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**Result:** the extension sees `sender = router_address` for every router-mediated swap. The pool admin has two losing options:

1. **Do not allowlist the router** → all router-mediated swaps revert, even for individually allowlisted users.
2. **Allowlist the router** → every user on the network can bypass the per-user gate by calling the public router.

Neither option preserves the intended per-user access control. The allowlist invariant is structurally broken for the router path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (KYC'd users, institutional partners, whitelisted addresses) can be freely accessed by any unprivileged user through `MetricOmmSimpleRouter`. The attacker can execute swaps that drain LP-owned liquidity or capture favorable oracle-anchored pricing that the pool admin intended to reserve for specific parties. This is a direct loss of LP principal and a broken core pool functionality (access-controlled swap).

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- No special role, token, or setup is required beyond having the pool address.
- The bypass is a single call: `router.exactInputSingle({pool: restrictedPool, ...})`.
- Any pool that uses `SwapAllowlistExtension` and also needs to support router-mediated swaps is affected.

---

### Recommendation

The `sender` forwarded to extensions should represent the **economic initiator** of the swap, not the immediate `msg.sender` of `pool.swap()`. Two complementary fixes:

1. **In the router:** pass the original `msg.sender` (the user) as the `recipient` or via `extensionData` so extensions can recover the true initiator.
2. **In `SwapAllowlistExtension`:** gate on a caller-supplied identity that the router attests to, or require the pool to be called directly (no router intermediary) for allowlisted pools.

The cleanest fix is for the router to encode the true user address in `extensionData` and for `SwapAllowlistExtension` to decode and verify it, similar to how Uniswap v4 uses `hookData` for caller attestation.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  admin allowlists router R: E.setAllowedToSwap(P, router, true)
  user Alice (not individually allowlisted) wants to swap

Attack:
  Alice calls router.exactInputSingle({pool: P, ...})
  → router calls P.swap(recipient, ...) with msg.sender = router
  → pool calls E.beforeSwap(router, ...)
  → E checks allowedSwapper[P][router] == true → passes
  → Alice's swap executes in the restricted pool

Result:
  Alice, who is not in the allowlist, successfully swaps in a pool
  that was intended to be restricted to specific counterparties.
  LP funds are exposed to unauthorized swap flow.
``` [6](#0-5) [4](#0-3)

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
