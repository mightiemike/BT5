### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps, every user — including non-allowlisted ones — can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the first argument (`sender`) to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the ABI call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The end user's allowlist status is never consulted.

This produces two concrete failure modes from the same root cause:

**Mode A — Allowlist bypass.** The pool admin allowlists the router address (a plausible action when wanting to enable router-mediated swaps for their curated pool). Because the extension checks the router's entry, every user who calls the router — including addresses the admin never allowlisted — passes the gate. The per-user allowlist is silently voided for all router-mediated swaps.

**Mode B — Broken periphery for legitimate users.** The pool admin allowlists specific EOAs. Those EOAs call the router. The extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap`. Allowlisted users cannot use the supported periphery path at all.

The structural parallel to the NFTFloorOracle H-01 is exact: in that report the index mapping pointed to the wrong feeder after a swap-and-pop removal; here the `sender` binding points to the wrong actor (router instead of user) after the call is routed through an intermediary. In both cases the guard operates on a corrupted identity and either fails open or fails closed against the wrong target.

---

### Impact Explanation

On a curated pool (KYC-gated, institutional, or access-controlled), the allowlist is the primary mechanism preventing unauthorized trading. Mode A voids that mechanism for all router-mediated swaps: any address can trade by calling `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`. Unauthorized traders can drain LP-owned token balances at oracle-quoted prices, causing direct loss of LP principal. Mode B prevents allowlisted users from using the standard periphery path, breaking core swap functionality for the intended user set.

---

### Likelihood Explanation

The pool admin must allowlist the router for Mode A to trigger. This is a plausible operational decision: an admin who wants to allow router-mediated swaps for their allowlisted users will naturally add the router to the allowlist, not realizing it grants access to all router callers. Mode B triggers unconditionally whenever the allowlist is used with the router and the router is not explicitly allowlisted — which is the default state.

---

### Recommendation

The extension must check the economic actor, not the intermediary. Two sound approaches:

1. **Forward the originating user in `extensionData`.** Require the router to encode the end user's address in `extensionData`; the extension decodes and checks that address. The pool's `beforeSwap` already forwards `extensionData` unchanged.

2. **Check `sender` only when it is a known EOA; otherwise decode from `extensionData`.** The extension can distinguish direct calls (EOA `sender`) from router calls (contract `sender`) and require the router to supply the user identity in the payload.

Do not use `tx.origin` — it breaks contract-wallet and multisig flows.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    (admin intends to allow router-mediated swaps for their allowlisted users)

Attack (Mode A):
  attacker = address not in allowedSwapper[pool]
  attacker calls: router.exactInputSingle({pool: pool, recipient: attacker, ...})
  router calls: pool.swap(attacker, ...)
  pool calls: extension.beforeSwap(router, attacker, ...)
  extension checks: allowedSwapper[pool][router] == true  → passes
  attacker swaps successfully despite never being allowlisted

Broken functionality (Mode B):
  pool admin calls: swapExtension.setAllowedToSwap(pool, alice, true)
  alice calls: router.exactInputSingle({pool: pool, recipient: alice, ...})
  router calls: pool.swap(alice, ...)
  pool calls: extension.beforeSwap(router, alice, ...)
  extension checks: allowedSwapper[pool][router] == false → NotAllowedToSwap
  alice cannot use the router despite being explicitly allowlisted
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
