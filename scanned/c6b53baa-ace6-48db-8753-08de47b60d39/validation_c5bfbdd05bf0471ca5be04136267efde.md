### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, enabling any user to bypass per-user swap access control when the router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `sender`, which is `msg.sender` from the pool's perspective — the immediate caller of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, `sender` equals the **router address**, not the actual user. A pool admin who allowlists the router to enable multi-hop routing for legitimate users inadvertently opens the pool to **all** users, completely defeating the per-user access control.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap()` enforces the allowlist as follows:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded from `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The router stores the original user in transient storage for the payment callback, but **never forwards the user's identity to the pool or the extension**. The pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

This creates an irreconcilable configuration gap:

| Router allowlisted? | Allowlisted user (direct) | Allowlisted user (router) | Non-allowlisted user (router) |
|---|---|---|---|
| No | ✅ Allowed | ❌ Blocked | ❌ Blocked |
| Yes | ✅ Allowed | ✅ Allowed | ✅ **Bypass** |

There is no configuration that simultaneously allows allowlisted users to use the router **and** blocks non-allowlisted users from using the router. The same applies to `exactInput` and `exactOutput` multi-hop paths, where every hop's `sender` is the router. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

When a pool admin allowlists the router address (a natural action to enable multi-hop routing for their KYC'd or institutional users), **any** address can bypass the swap allowlist by routing through `MetricOmmSimpleRouter`. The allowlist — the sole access-control mechanism for curated pools — is rendered inoperative for all router-mediated swaps. Non-allowlisted users gain full swap access to a pool explicitly designed to restrict counterparties, which can cause direct loss of LP assets if the pool's pricing or liquidity is calibrated for a specific set of trusted counterparties.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router, which is a reasonable and expected operational action for any curated pool that wants to support multi-hop routing for its legitimate users. The admin is not acting maliciously; the system simply provides no mechanism to allowlist the router for specific users only. Any pool that enables router-based swaps for its allowlisted users is silently exposed to all users.

---

### Recommendation

The `SwapAllowlistExtension` must check the **economically relevant actor** — the address that initiated the transaction and will pay the input tokens — not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Extension-data identity forwarding**: The router encodes the original `msg.sender` into `extensionData` for each hop. The extension decodes and verifies it (requires a trusted router registry or a signature scheme).
2. **Router-aware allowlist**: Add a separate `allowedRouter` mapping. When `sender` is a known router, decode the original user from `extensionData` and check `allowedSwapper[pool][originalUser]` instead.

The `DepositAllowlistExtension` correctly gates by `owner` (the economically relevant actor for deposits) and does not share this flaw. [6](#0-5) 

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension (allowAllSwappers = false).
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router to enable multi-hop for userA
4. userB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: userB, ...})
5. Router calls pool.swap(userB, ...) with msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
8. userB's swap executes successfully — allowlist bypassed.
``` [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
