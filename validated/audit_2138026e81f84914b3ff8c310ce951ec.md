### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router**, not the user. The extension therefore checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. This creates an irresolvable policy contradiction: if the pool admin allowlists the router so that legitimate users can use it, every unprivileged user gains unrestricted swap access to the curated pool.

---

### Finding Description

**Trace through the call stack:**

**Step 1 — Router calls pool with itself as `msg.sender`:**

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
``` [1](#0-0) 

The pool receives this call with `msg.sender = address(router)`.

**Step 2 — Pool passes `msg.sender` (router) as `sender` to the hook:**

```solidity
_beforeSwap(
    msg.sender,   // ← router address, not the user
    recipient,
    ...
);
``` [2](#0-1) 

**Step 3 — `ExtensionCalling` forwards the router address as `sender`:**

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)  // sender = router
)
``` [3](#0-2) 

**Step 4 — `SwapAllowlistExtension` checks the router address, not the user:**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [4](#0-3) 

Here `msg.sender` is the pool and `sender` is the **router address**. The check `allowedSwapper[pool][router]` is evaluated — the actual user's address is never consulted.

**The irresolvable contradiction for pool admins:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on-chain can bypass the allowlist via the router |

The same wrong-actor binding applies to `exactInput` (all hops call the pool with `msg.sender = router`) and `exactOutputSingle`. [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd users, protocol-owned contracts, or whitelisted market makers) is completely defeated. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and execute swaps on the restricted pool, draining LP liquidity at oracle-derived prices. Because the router is a public, permissionless contract, no special privilege or setup is required beyond holding the input token.

---

### Likelihood Explanation

- The router is a deployed, public periphery contract — any EOA or contract can call it.
- The bypass requires zero privileged access, no special tokens, and no multi-transaction setup.
- The only precondition is that the pool admin has allowlisted the router (which is the natural action when the admin wants legitimate users to be able to use the router).
- The `SwapAllowlistExtension` is a production-ready extension explicitly designed for curated pools. [6](#0-5) 

---

### Recommendation

The extension must recover the original user's address rather than trusting the `sender` argument forwarded by the pool. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to sign or commit the user identity in a way the extension can trust.

2. **Check `sender` against a router registry and then verify the payer stored in transient storage**: The extension recognises the router as a trusted forwarder and reads the actual payer from the router's transient context (already stored at `_getPayer()` in the router's callback context).

3. **Require direct pool calls for allowlisted pools**: Document and enforce that `SwapAllowlistExtension` pools must not allowlist the router; users must call `pool.swap()` directly.

The cleanest fix is option 2: the router already stores the real payer in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`) — exposing a `getPayer()` view on the router and having the extension call it when `sender` is the router would recover the correct identity without breaking the existing call path. [7](#0-6) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = extension1.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router so legitimate users can use it
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams({
          pool: pool,
          recipient: attacker,
          tokenIn: token0,
          amountIn: 1e18,
          ...
      }))

Result:
  - pool.swap() is called with msg.sender = router
  - _beforeSwap passes sender = router to SwapAllowlistExtension
  - allowedSwapper[pool][router] == true  → check passes
  - attacker receives token1 output from the curated pool
  - The allowlist invariant is broken: attacker was never allowlisted
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
