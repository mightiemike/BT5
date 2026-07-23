### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (used as the namespace key — correct), and `sender` is the first argument forwarded by the pool. The pool sets that argument to its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

So `msg.sender` to the pool is the **router address**, and the extension receives `sender = address(router)`. The extension then checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

The same misbinding occurs in `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

---

### Impact Explanation

**Scenario — Full allowlist bypass:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses.
2. To support the standard periphery path, the admin allowlists the router: `setAllowedToSwap(pool, router, true)`.
3. Any unprivileged user calls `router.exactInputSingle(...)` targeting the curated pool.
4. The extension receives `sender = router`, finds `allowedSwapper[pool][router] == true`, and passes.
5. The non-allowlisted user's swap executes against the curated pool, receiving pool output tokens.

The allowlist is completely defeated. Every user who can call the router — i.e., the entire public — can trade on a pool that was designed to be restricted. This constitutes a broken core pool functionality (access control) with direct loss potential: LP funds in a curated pool are exposed to unrestricted arbitrage or toxic flow that the allowlist was meant to prevent.

**Scenario — Allowlisted users locked out (secondary):**

If the admin does *not* allowlist the router, then legitimately allowlisted users cannot use the router at all (their swaps revert with `NotAllowedToSwap` because the router's address is not on the list). They are forced to call the pool directly, losing slippage protection, deadline enforcement, and multi-hop routing provided by the periphery.

---

### Likelihood Explanation

- The trigger is a standard, public, documented periphery call (`exactInputSingle`, `exactInput`, etc.) — no special role or privileged access required.
- The pool admin allowlisting the router is the expected operational step to make the router work with an allowlisted pool; the bug is latent in every such deployment.
- No malicious setup is required; the attacker only needs to call the public router with a valid pool address and token approval.

---

### Recommendation

The extension must gate the **end user**, not the intermediary. Two options:

1. **Pass the original caller through the router.** The router stores `msg.sender` in transient storage (already done for the payer context). Expose it as a `recipient`-style parameter or a dedicated transient slot that the extension can read, and have the extension check that value instead of `sender`.

2. **Check `recipient` instead of `sender` in the extension.** The `recipient` is the address that receives output tokens and is set by the end user. For a swap allowlist, gating `recipient` is semantically closer to "who benefits from this swap." The extension signature already receives `recipient` as the second argument (currently ignored with `address`).

The cleanest fix consistent with the existing architecture is option 1: the router should forward the original `msg.sender` as a verifiable identity, and the extension should check that identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)   // to enable router path
  - Admin does NOT allowlist attacker

Attack:
  1. attacker calls router.exactInputSingle({
         pool: curated_pool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls pool.swap(attacker, true, X, ...)
     → msg.sender to pool = router
  3. Pool calls _beforeSwap(router, attacker, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router] == true → PASSES
  5. Swap executes; attacker receives output tokens

Result: Non-allowlisted attacker successfully swaps on a curated pool.
```

The `FullMetricExtensionTest` integration test at line 68–73 confirms the intended behavior is that only `callers[0]` (the allowlisted address) can swap — but it only tests direct pool calls, not router-mediated calls, leaving the bypass untested. [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
