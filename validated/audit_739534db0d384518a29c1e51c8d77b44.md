### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` at the pool is the **router address**, not the originating user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including explicitly disallowed ones — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check: [1](#0-0) 

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

Here `msg.sender` is the pool (the extension's caller), and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call. The pool passes this via `_beforeSwap(msg.sender, recipient, ...)`: [2](#0-1) 

(Analogous to `addLiquidity` which explicitly passes `msg.sender` as `sender` to `_beforeAddLiquidity`.) [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, the router calls `pool.swap(recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` seen by the pool is the **router contract**, not the originating EOA. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The router forwards no user-identity information to the extension system.

The same substitution occurs in every router path:
- `exactInput` multi-hop: router calls each pool directly.
- `exactOutputSingle`: router calls pool directly.
- `exactOutput` recursive callback: the router (inside `_exactOutputIterateCallback`) calls each intermediate pool. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool admin who wants to allow router-mediated swaps for their allowlisted users will add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any** user — including those explicitly excluded — can call any router entry point and the extension will pass, because the check resolves to `allowedSwapper[pool][router] == true`. The per-user allowlist is completely neutralised for all router paths. Disallowed users gain full swap access to a curated pool, enabling unauthorized extraction of value, front-running of allowlisted participants, or disruption of pools designed for restricted audiences (e.g., institutional, KYC-gated, or whitelist-only pools).

---

### Likelihood Explanation

The scenario is highly likely in practice. A pool admin who deploys a curated pool with `SwapAllowlistExtension` and also wants users to be able to use the standard router will naturally allowlist the router address. The Solidity interface and NatSpec of `SwapAllowlistExtension` give no indication that doing so opens the allowlist to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economically relevant actor**, not the direct pool caller. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the originating user's address in `extensionData`; the extension decodes and checks it. This requires a trusted router or a signed attestation.
2. **Recipient-based check**: For swap allowlists, check `recipient` (the address receiving output tokens) in addition to or instead of `sender`, since the recipient is the economic beneficiary and is harder to spoof.
3. **Documentation / revert**: If the extension is intentionally caller-only, document that allowlisting the router opens access to all users, and provide a separate `RouterSwapAllowlistExtension` that decodes user identity from `extensionData`.

---

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][router] = true   // to enable router swaps
  pool admin: allowedSwapper[pool][alice]  = false  // alice is disallowed

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: alice, ...})
    → router calls pool.swap(alice, zeroForOne, amount, limit, "", extensionData)
    → pool calls _beforeSwap(msg.sender=router, alice, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; alice receives output tokens

Result:
  alice swaps successfully on a pool that was supposed to block her.
  The per-user allowlist is bypassed entirely via the router.
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
