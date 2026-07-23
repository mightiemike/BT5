### Title
`SwapAllowlistExtension` gates the router address instead of the end-user identity, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for any user), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap` (line 37):**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`, which is `msg.sender` of `MetricOmmPool.swap()`. [1](#0-0) 

**How the pool forwards `msg.sender` to the extension (`MetricOmmPool.swap`, lines 230–240):**

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
    extensionData
);
``` [2](#0-1) 

**How the router calls the pool (`MetricOmmSimpleRouter.exactInputSingle`, lines 72–80):**

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
```

The router calls `pool.swap()` directly. `msg.sender` seen by the pool is the **router address**. The original end-user address (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the payment callback — it is never forwarded to the pool or to any extension hook. [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The invariant break:**

The allowlist is keyed as `allowedSwapper[pool][sender]`. For direct pool calls, `sender` is the end user — the allowlist works. For router-mediated calls, `sender` is the router. The pool admin must allowlist the router address to permit any router-mediated swap. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that goes through the router, regardless of who the actual end user is. There is no mechanism in the router to authenticate or forward the original caller's identity to the extension. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, institutional participants, or whitelisted market makers) loses that restriction entirely for router-mediated swaps once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and execute swaps on the restricted pool. This breaks the core pool access-control invariant and can expose restricted LP positions to unauthorized counterparties, enabling unauthorized extraction of pool liquidity at oracle-derived prices.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router — a necessary and expected configuration step for any pool that intends to support router-mediated swaps for its permitted users. The admin cannot simultaneously allow router-mediated swaps for allowlisted users and block them for non-allowlisted users, because the extension sees only the router address. Any pool that uses `SwapAllowlistExtension` together with `MetricOmmSimpleRouter` is affected as soon as the router is added to the allowlist.

---

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor — the end user — not the intermediary router. Two approaches:

1. **Authenticated forwarding**: The router encodes the original `msg.sender` into `extensionData` and signs or authenticates it. The extension decodes and verifies the claimed identity. This requires a trust relationship between the extension and the router.

2. **Separate router allowlist**: Introduce a two-level check: if `sender` is a known router, decode the end-user address from `extensionData` and check that address against the allowlist. The extension must trust the router to supply an honest caller address.

3. **Direct-only policy**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level (e.g., reject pool configurations that combine a swap allowlist with a public router).

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension as a before-swap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary to allow any router-mediated swap.
3. Alice (allowlisted) and Bob (not allowlisted) both call
   MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...}).
4. For both calls, pool.swap() sees msg.sender = router.
5. SwapAllowlistExtension checks allowedSwapper[pool][router] → true for both.
6. Bob's swap succeeds despite not being on the allowlist.

Assert: Bob receives output tokens from the restricted pool.
``` [1](#0-0) [6](#0-5) [2](#0-1)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
